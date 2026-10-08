/*
 * tmt70mon.c -- Phase 1: production Windows Print Monitor (Monitor2) for
 * tmt70print.
 *
 * Thin transport shim, not a reimplementation: every job's bytes are
 * accumulated to a temp file across WritePort() calls, then handed to the
 * *existing*, unmodified tmt70print CLI as a subprocess on EndDocPort() --
 * all ESC/POS framing, options handling and transport (WinUSB/TCP/spooler)
 * logic stays in win/tmt70win/{job,text,sinks}.py exactly as it is for the
 * manual-CLI path. See win/monitor/README.md for the full design rationale
 * and win/monitor/spike/NOTES.md for the Phase 0 findings this builds on.
 *
 * v1 scope (see docs/WINDOWS.md once published): ONE fixed port ("TMT70:"),
 * text-only (paired with the inbox "Generic / Text Only" driver -- no
 * image/graphics parity yet, that's the separate Phase 2 research spike).
 * Configuration (which tmt70print options/device to use) lives in the
 * registry under this monitor's own key, written by register-monitor.ps1;
 * there's no port-properties UI in v1 (ConfigurePort is a no-op).
 *
 * Reference: MONITOR2/MONITORINIT layouts from MinGW-w64's bundled
 * <winsplp.h>, same as the Phase 0 spike -- not from memory.
 */

#define WIN32_LEAN_AND_MEAN
#define NTDDI_VERSION 0x0A000000   /* NTDDI_WIN10 */
#define _WIN32_WINNT  0x0A00       /* _WIN32_WINNT_WIN10 */

#include <windows.h>
#include <winspool.h>
#include <winsplp.h>
#include <stdio.h>
#include <time.h>
#include <wchar.h>

static const WCHAR PORT_NAME[]    = L"TMT70:";
static const WCHAR MONITOR_DESC[] = L"TMT70 Monitor";
static const WCHAR PORT_DESC[]    = L"EPSON TM-T70II (tmt70print)";
static const WCHAR CONFIG_KEY[]   =
    L"SYSTEM\\CurrentControlSet\\Control\\Print\\Monitors\\TMT70 Monitor\\Config";

/* Subprocess wait timeout -- a hung USB/TCP transport must not hang the
 * spooler's print pipeline indefinitely. */
#define TMT70_TIMEOUT_MS 30000

/* ------------------------------------------------------------------ */
/* Logging -- same rationale as the Phase 0 spike: a hardcoded,         */
/* universally-writable path (not resolved via %ProgramData%, which can */
/* differ under an isolated per-job process context), plus an           */
/* unconditional Event Log fallback so a logging failure is visible      */
/* instead of silent. See spike/NOTES.md bug #2.                        */
/* ------------------------------------------------------------------ */

static const wchar_t *LOG_PATH = L"C:\\Windows\\Temp\\tmt70mon.log";

static void ReportToEventLog(const wchar_t *text)
{
    HANDLE hSource = RegisterEventSourceW(NULL, L"Application");
    if (!hSource) return;
    LPCWSTR strings[1] = { text };
    ReportEventW(hSource, EVENTLOG_INFORMATION_TYPE, 0, 0, NULL, 1, 0, strings, NULL);
    DeregisterEventSource(hSource);
}

static void LogLine(const char *tag, const char *msg)
{
    time_t now = time(NULL);
    struct tm tmv;
    localtime_s(&tmv, &now);
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", &tmv);

    char line[1024];
    _snprintf(line, sizeof line, "[%s] %-16s %s", ts, tag, msg ? msg : "");
    line[sizeof line - 1] = 0;

    FILE *f = _wfopen(LOG_PATH, L"a");
    if (f) {
        fprintf(f, "%s\n", line);
        fclose(f);
    }

    wchar_t wline[1024];
    MultiByteToWideChar(CP_ACP, 0, line, -1, wline, 1024);
    ReportToEventLog(wline);
}

static void LogBytesPreview(const char *tag, const BYTE *data, DWORD len)
{
    char line[400];
    int off = _snprintf(line, sizeof line, "len=%lu hex=", (unsigned long)len);
    DWORD hexShow = len < 24 ? len : 24;
    for (DWORD i = 0; i < hexShow && off < (int)sizeof(line) - 4; i++) {
        off += _snprintf(line + off, sizeof(line) - off, "%02X ", data ? data[i] : 0);
    }
    off += _snprintf(line + off, sizeof(line) - off, "preview=");
    DWORD show = len < 128 ? len : 128;
    for (DWORD i = 0; i < show && off < (int)sizeof(line) - 3; i++) {
        BYTE b = data ? data[i] : 0;
        /* printable ASCII as itself, everything else as a dot -- this is a
         * human-readability preview, not a hex dump (see WritePort itself
         * for the exact byte handling / form-feed stripping). */
        line[off++] = (b >= 0x20 && b < 0x7F) ? (char)b : '.';
    }
    if (off < (int)sizeof(line)) line[off] = 0;
    line[sizeof line - 1] = 0;
    LogLine(tag, line);
}

/* ------------------------------------------------------------------ */
/* Registry config -- written by register-monitor.ps1 at install time. */
/* ------------------------------------------------------------------ */

static BOOL ReadConfigString(const wchar_t *valueName, wchar_t *outBuf,
                              DWORD outCch, const wchar_t *fallback)
{
    HKEY hKey;
    LONG rc = RegOpenKeyExW(HKEY_LOCAL_MACHINE, CONFIG_KEY, 0, KEY_READ, &hKey);
    if (rc == ERROR_SUCCESS) {
        DWORD type = 0, cb = outCch * sizeof(wchar_t);
        rc = RegQueryValueExW(hKey, valueName, NULL, &type, (LPBYTE)outBuf, &cb);
        RegCloseKey(hKey);
        if (rc == ERROR_SUCCESS && type == REG_SZ) return TRUE;
    }
    if (fallback) {
        wcsncpy(outBuf, fallback, outCch - 1);
        outBuf[outCch - 1] = 0;
        return TRUE;
    }
    outBuf[0] = 0;
    return FALSE;
}

/* ------------------------------------------------------------------ */
/* Per-port state -- a real heap context, unlike the Phase 0 spike's    */
/* dummy fixed handle: we need to accumulate bytes across WritePort     */
/* calls between StartDocPort and EndDocPort.                           */
/* ------------------------------------------------------------------ */

typedef struct {
    HANDLE  hJobFile;               /* current job's accumulation file, or INVALID_HANDLE_VALUE */
    wchar_t jobPath[MAX_PATH];
    DWORD   jobId;
} PORT_CTX;

static BOOL OpenJobFile(PORT_CTX *ctx)
{
    wchar_t tempDir[MAX_PATH];
    if (!GetTempPathW(MAX_PATH, tempDir)) {
        wcsncpy(tempDir, L"C:\\Windows\\Temp\\", MAX_PATH - 1);
    }
    if (!GetTempFileNameW(tempDir, L"t70", 0, ctx->jobPath)) {
        LogLine("StartDocPort", "GetTempFileNameW failed");
        return FALSE;
    }
    /* GetTempFileNameW already created an empty file at jobPath; reopen it
     * for writing the accumulated job bytes. */
    ctx->hJobFile = CreateFileW(ctx->jobPath, GENERIC_WRITE, FILE_SHARE_READ, NULL,
                                 CREATE_ALWAYS, FILE_ATTRIBUTE_TEMPORARY, NULL);
    if (ctx->hJobFile == INVALID_HANDLE_VALUE) {
        LogLine("StartDocPort", "CreateFileW on temp job file failed");
        return FALSE;
    }
    return TRUE;
}

/* Runs tmt70print against the finished job file. Returns TRUE on a clean
 * (exit code 0) run. All configuration comes from the registry -- see
 * register-monitor.ps1 for what writes PythonExe/ScriptPath/Device/ExtraArgs. */
static BOOL RunTmt70Print(const wchar_t *jobFile, DWORD jobId)
{
    wchar_t pythonExe[MAX_PATH], scriptPath[MAX_PATH];
    wchar_t device[512], extraArgs[512];

    if (!ReadConfigString(L"PythonExe", pythonExe, MAX_PATH, NULL)) {
        LogLine("EndDocPort", "config missing: PythonExe (run register-monitor.ps1)");
        return FALSE;
    }
    if (!ReadConfigString(L"ScriptPath", scriptPath, MAX_PATH, NULL)) {
        LogLine("EndDocPort", "config missing: ScriptPath (run register-monitor.ps1)");
        return FALSE;
    }
    ReadConfigString(L"Device", device, 512, L"tmt70://04b8:0202");
    ReadConfigString(L"ExtraArgs", extraArgs, 512, L"");

    wchar_t cmdLine[2048];
    _snwprintf(cmdLine, 2048,
               L"\"%s\" \"%s\" \"%s\" --device \"%s\" --kind text -v %s",
               pythonExe, scriptPath, jobFile, device, extraArgs);
    cmdLine[2047] = 0;

    SECURITY_ATTRIBUTES sa;
    sa.nLength = sizeof(sa);
    sa.lpSecurityDescriptor = NULL;
    sa.bInheritHandle = TRUE;

    HANDLE hNull = CreateFileW(L"NUL", GENERIC_READ, FILE_SHARE_READ, &sa,
                                OPEN_EXISTING, 0, NULL);
    HANDLE hLog  = CreateFileW(L"C:\\Windows\\Temp\\tmt70mon-lastjob.log",
                                GENERIC_WRITE, FILE_SHARE_READ, &sa,
                                CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);

    STARTUPINFOW si;
    ZeroMemory(&si, sizeof si);
    si.cb = sizeof si;
    si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    si.hStdInput  = hNull;
    si.hStdOutput = hLog;
    si.hStdError  = hLog;

    PROCESS_INFORMATION pi;
    ZeroMemory(&pi, sizeof pi);

    BOOL ok = CreateProcessW(NULL, cmdLine, NULL, NULL, TRUE,
                              CREATE_NO_WINDOW, NULL, NULL, &si, &pi);

    if (hNull != INVALID_HANDLE_VALUE) CloseHandle(hNull);
    if (hLog  != INVALID_HANDLE_VALUE) CloseHandle(hLog);

    if (!ok) {
        char msg[128];
        _snprintf(msg, sizeof msg, "job=%lu CreateProcessW failed, error=%lu",
                  (unsigned long)jobId, (unsigned long)GetLastError());
        LogLine("EndDocPort", msg);
        return FALSE;
    }

    DWORD wait = WaitForSingleObject(pi.hProcess, TMT70_TIMEOUT_MS);
    DWORD exitCode = 1;
    if (wait == WAIT_TIMEOUT) {
        char msg[64];
        _snprintf(msg, sizeof msg, "job=%lu timed out after %ds, terminating",
                  (unsigned long)jobId, TMT70_TIMEOUT_MS / 1000);
        LogLine("EndDocPort", msg);
        TerminateProcess(pi.hProcess, 1);
    } else {
        GetExitCodeProcess(pi.hProcess, &exitCode);
    }
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    char msg[128];
    _snprintf(msg, sizeof msg, "job=%lu tmt70print exit=%lu (see tmt70mon-lastjob.log for its own output)",
              (unsigned long)jobId, (unsigned long)exitCode);
    LogLine("EndDocPort", msg);

    return exitCode == 0;
}

/* ------------------------------------------------------------------ */
/* Monitor2 callbacks                                                   */
/* ------------------------------------------------------------------ */

static WINBOOL WINAPI Mon_EnumPorts(HANDLE hMonitor, LPWSTR pName, DWORD Level,
                                     LPBYTE pPorts, DWORD cbBuf,
                                     LPDWORD pcbNeeded, LPDWORD pcReturned)
{
    (void)hMonitor; (void)pName;

    if (Level != 1 && Level != 2) {
        *pcbNeeded = 0;
        *pcReturned = 0;
        SetLastError(ERROR_INVALID_LEVEL);
        return FALSE;
    }

    DWORD structSize = (Level == 1) ? sizeof(PORT_INFO_1W) : sizeof(PORT_INFO_2W);
    DWORD nameBytes   = sizeof(PORT_NAME);
    DWORD descBytes   = (Level == 2) ? (sizeof(MONITOR_DESC) + sizeof(PORT_DESC)) : 0;
    DWORD needed      = structSize + nameBytes + descBytes;

    *pcbNeeded = needed;
    if (pPorts == NULL || cbBuf < needed) {
        *pcReturned = 0;
        SetLastError(ERROR_INSUFFICIENT_BUFFER);
        return FALSE;
    }

    BYTE *strPtr = pPorts + structSize;
    if (Level == 1) {
        PORT_INFO_1W *info = (PORT_INFO_1W *)pPorts;
        memcpy(strPtr, PORT_NAME, nameBytes);
        info->pName = (LPWSTR)strPtr;
    } else {
        PORT_INFO_2W *info = (PORT_INFO_2W *)pPorts;
        memcpy(strPtr, PORT_NAME, nameBytes);
        info->pPortName = (LPWSTR)strPtr;
        strPtr += nameBytes;

        memcpy(strPtr, MONITOR_DESC, sizeof(MONITOR_DESC));
        info->pMonitorName = (LPWSTR)strPtr;
        strPtr += sizeof(MONITOR_DESC);

        memcpy(strPtr, PORT_DESC, sizeof(PORT_DESC));
        info->pDescription = (LPWSTR)strPtr;

        info->fPortType = PORT_TYPE_WRITE;
        info->Reserved  = 0;
    }

    *pcReturned = 1;
    return TRUE;
}

static WINBOOL WINAPI Mon_OpenPort(HANDLE hMonitor, LPWSTR pName, PHANDLE pHandle)
{
    (void)hMonitor; (void)pName;
    PORT_CTX *ctx = (PORT_CTX *)HeapAlloc(GetProcessHeap(), HEAP_ZERO_MEMORY, sizeof(PORT_CTX));
    if (!ctx) return FALSE;
    ctx->hJobFile = INVALID_HANDLE_VALUE;
    *pHandle = (HANDLE)ctx;
    return TRUE;
}

static WINBOOL WINAPI Mon_OpenPortEx(HANDLE hMonitor, HANDLE hMonitorPort,
                                      LPWSTR pPortName, LPWSTR pPrinterName,
                                      PHANDLE pHandle, PMONITOR2 pMonitor2)
{
    (void)hMonitorPort; (void)pPrinterName; (void)pMonitor2;
    return Mon_OpenPort(hMonitor, pPortName, pHandle);
}

static WINBOOL WINAPI Mon_StartDocPort(HANDLE hPort, LPWSTR pPrinterName,
                                        DWORD JobId, DWORD Level, LPBYTE pDocInfo)
{
    (void)pPrinterName; (void)Level; (void)pDocInfo;
    PORT_CTX *ctx = (PORT_CTX *)hPort;
    ctx->jobId = JobId;
    if (!OpenJobFile(ctx)) return FALSE;
    char msg[64];
    _snprintf(msg, sizeof msg, "job=%lu", (unsigned long)JobId);
    LogLine("StartDocPort", msg);
    return TRUE;
}

static WINBOOL WINAPI Mon_WritePort(HANDLE hPort, LPBYTE pBuffer, DWORD cbBuf,
                                     LPDWORD pcbWritten)
{
    PORT_CTX *ctx = (PORT_CTX *)hPort;

    /* Some job paths may not call StartDocPort first; be defensive. */
    if (ctx->hJobFile == INVALID_HANDLE_VALUE) {
        ctx->jobId = 0;
        if (!OpenJobFile(ctx)) {
            *pcbWritten = 0;
            return FALSE;
        }
    }

    /* Strip 0x0C (form feed / page eject) -- the "Generic / Text Only"
     * print processor emits one per page, but it's not meaningful ESC/POS
     * content and would otherwise render as a stray glyph. Multi-page
     * (multi-FF) documents are a known v1 limitation: they're printed as
     * one continuous job, not split at the FF boundaries -- see
     * win/monitor/README.md. */
    BYTE *filtered = (BYTE *)HeapAlloc(GetProcessHeap(), 0, cbBuf ? cbBuf : 1);
    DWORD filteredLen = 0;
    if (filtered) {
        for (DWORD i = 0; i < cbBuf; i++) {
            if (pBuffer[i] != 0x0C) filtered[filteredLen++] = pBuffer[i];
        }
    }

    BOOL ok = TRUE;
    if (filtered && filteredLen > 0) {
        DWORD written = 0;
        ok = WriteFile(ctx->hJobFile, filtered, filteredLen, &written, NULL)
             && written == filteredLen;
    }
    if (filtered) HeapFree(GetProcessHeap(), 0, filtered);

    if (!ok) {
        *pcbWritten = 0;
        LogLine("WritePort", "WriteFile to job accumulation file failed");
        return FALSE;
    }

    LogBytesPreview("WritePort", pBuffer, cbBuf);
    *pcbWritten = cbBuf;   /* report the spooler's original count, not our filtered one */
    return TRUE;
}

static WINBOOL WINAPI Mon_ReadPort(HANDLE hPort, LPBYTE pBuffer, DWORD cbBuffer,
                                    LPDWORD pcbRead)
{
    (void)hPort; (void)pBuffer; (void)cbBuffer;
    *pcbRead = 0;
    return TRUE;
}

static WINBOOL WINAPI Mon_EndDocPort(HANDLE hPort)
{
    PORT_CTX *ctx = (PORT_CTX *)hPort;

    if (ctx->hJobFile == INVALID_HANDLE_VALUE) {
        LogLine("EndDocPort", "no job data accumulated, nothing to print");
        return TRUE;
    }

    CloseHandle(ctx->hJobFile);
    ctx->hJobFile = INVALID_HANDLE_VALUE;

    BOOL ok = RunTmt70Print(ctx->jobPath, ctx->jobId);

    DeleteFileW(ctx->jobPath);
    ctx->jobPath[0] = 0;

    /* Returning FALSE here surfaces the failure to the spooler as a job
     * error rather than a silent "Complete" -- deliberate per the plan. */
    return ok;
}

static WINBOOL WINAPI Mon_ClosePort(HANDLE hPort)
{
    PORT_CTX *ctx = (PORT_CTX *)hPort;
    if (ctx->hJobFile != INVALID_HANDLE_VALUE) {
        CloseHandle(ctx->hJobFile);
        DeleteFileW(ctx->jobPath);
    }
    HeapFree(GetProcessHeap(), 0, ctx);
    return TRUE;
}

static WINBOOL WINAPI Mon_AddPort(HANDLE hMonitor, LPWSTR pName, HWND hWnd, LPWSTR pMonitorName)
{
    (void)hMonitor; (void)pName; (void)hWnd; (void)pMonitorName;
    SetLastError(ERROR_NOT_SUPPORTED);   /* fixed single port; nothing to add in v1 */
    return FALSE;
}

static WINBOOL WINAPI Mon_AddPortEx(HANDLE hMonitor, LPWSTR pName, DWORD Level,
                                     LPBYTE lpBuffer, LPWSTR lpMonitorName)
{
    (void)hMonitor; (void)pName; (void)Level; (void)lpBuffer; (void)lpMonitorName;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

static WINBOOL WINAPI Mon_ConfigurePort(HANDLE hMonitor, LPWSTR pName, HWND hWnd, LPWSTR pPortName)
{
    (void)hMonitor; (void)pName; (void)pPortName;
    if (hWnd) {
        MessageBoxW(hWnd,
            L"TMT70 Monitor has no port-properties UI in this version.\n\n"
            L"Configure the target device by editing:\n"
            L"HKLM\\SYSTEM\\CurrentControlSet\\Control\\Print\\Monitors\\TMT70 Monitor\\Config\n"
            L"(see register-monitor.ps1), then restart the Spooler service.",
            L"TMT70 Monitor", MB_OK | MB_ICONINFORMATION);
    }
    return TRUE;
}

static WINBOOL WINAPI Mon_DeletePort(HANDLE hMonitor, LPWSTR pName, HWND hWnd, LPWSTR pPortName)
{
    (void)hMonitor; (void)pName; (void)hWnd; (void)pPortName;
    return TRUE;   /* fixed port; nothing to actually remove */
}

static WINBOOL WINAPI Mon_GetPrinterDataFromPort(HANDLE hPort, DWORD ControlID,
    LPWSTR pValueName, LPWSTR lpInBuffer, DWORD cbInBuffer,
    LPWSTR lpOutBuffer, DWORD cbOutBuffer, LPDWORD lpcbReturned)
{
    (void)hPort; (void)ControlID; (void)pValueName; (void)lpInBuffer; (void)cbInBuffer;
    (void)lpOutBuffer; (void)cbOutBuffer;
    *lpcbReturned = 0;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

static WINBOOL WINAPI Mon_SetPortTimeOuts(HANDLE hPort, LPCOMMTIMEOUTS lpCTO, DWORD reserved)
{
    (void)hPort; (void)lpCTO; (void)reserved;
    return TRUE;
}

static WINBOOL WINAPI Mon_XcvOpenPort(HANDLE hMonitor, LPCWSTR pszObject,
                                       ACCESS_MASK GrantedAccess, PHANDLE phXcv)
{
    (void)hMonitor; (void)pszObject; (void)GrantedAccess;
    *phXcv = NULL;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

static DWORD WINAPI Mon_XcvDataPort(HANDLE hXcv, LPCWSTR pszDataName,
    PBYTE pInputData, DWORD cbInputData, PBYTE pOutputData, DWORD cbOutputData,
    PDWORD pcbOutputNeeded)
{
    (void)hXcv; (void)pszDataName; (void)pInputData; (void)cbInputData;
    (void)pOutputData; (void)cbOutputData;
    if (pcbOutputNeeded) *pcbOutputNeeded = 0;
    return ERROR_NOT_SUPPORTED;
}

static WINBOOL WINAPI Mon_XcvClosePort(HANDLE hXcv)
{
    (void)hXcv;
    return TRUE;
}

static VOID WINAPI Mon_Shutdown(HANDLE hMonitor)
{
    (void)hMonitor;
    LogLine("Shutdown", "spooler is unloading the monitor");
}

#if (NTDDI_VERSION >= NTDDI_WINXP)
static DWORD WINAPI Mon_SendRecvBidiDataFromPort(HANDLE hPort, DWORD dwAccessBit,
    LPCWSTR pAction, PBIDI_REQUEST_CONTAINER pReqData, PBIDI_RESPONSE_CONTAINER *ppResData)
{
    (void)hPort; (void)dwAccessBit; (void)pAction; (void)pReqData;
    if (ppResData) *ppResData = NULL;
    return ERROR_NOT_SUPPORTED;
}
#endif

#if (NTDDI_VERSION >= NTDDI_WIN7)
static DWORD WINAPI Mon_NotifyUsedPorts(HANDLE hMonitor, DWORD cPorts, PCWSTR *ppszPorts)
{
    (void)hMonitor; (void)cPorts; (void)ppszPorts;
    return NO_ERROR;
}

static DWORD WINAPI Mon_NotifyUnusedPorts(HANDLE hMonitor, DWORD cPorts, PCWSTR *ppszPorts)
{
    (void)hMonitor; (void)cPorts; (void)ppszPorts;
    return NO_ERROR;
}
#endif

/* ------------------------------------------------------------------ */
/* Entry point                                                          */
/* ------------------------------------------------------------------ */

static MONITOR2 g_Monitor2;

__declspec(dllexport)
LPMONITOR2 WINAPI InitializePrintMonitor2(PMONITORINIT pMonitorInit, PHANDLE phMonitor)
{
    (void)pMonitorInit;
    LogLine("Init", "spooler is initializing TMT70 Monitor");

    ZeroMemory(&g_Monitor2, sizeof(g_Monitor2));
    g_Monitor2.cbSize                   = sizeof(g_Monitor2);
    g_Monitor2.pfnEnumPorts              = Mon_EnumPorts;
    g_Monitor2.pfnOpenPort               = Mon_OpenPort;
    g_Monitor2.pfnOpenPortEx             = Mon_OpenPortEx;
    g_Monitor2.pfnStartDocPort           = Mon_StartDocPort;
    g_Monitor2.pfnWritePort              = Mon_WritePort;
    g_Monitor2.pfnReadPort               = Mon_ReadPort;
    g_Monitor2.pfnEndDocPort             = Mon_EndDocPort;
    g_Monitor2.pfnClosePort              = Mon_ClosePort;
    g_Monitor2.pfnAddPort                = Mon_AddPort;
    g_Monitor2.pfnAddPortEx              = Mon_AddPortEx;
    g_Monitor2.pfnConfigurePort          = Mon_ConfigurePort;
    g_Monitor2.pfnDeletePort             = Mon_DeletePort;
    g_Monitor2.pfnGetPrinterDataFromPort = Mon_GetPrinterDataFromPort;
    g_Monitor2.pfnSetPortTimeOuts        = Mon_SetPortTimeOuts;
    g_Monitor2.pfnXcvOpenPort            = Mon_XcvOpenPort;
    g_Monitor2.pfnXcvDataPort            = Mon_XcvDataPort;
    g_Monitor2.pfnXcvClosePort           = Mon_XcvClosePort;
    g_Monitor2.pfnShutdown               = Mon_Shutdown;
#if (NTDDI_VERSION >= NTDDI_WINXP)
    g_Monitor2.pfnSendRecvBidiDataFromPort = Mon_SendRecvBidiDataFromPort;
#endif
#if (NTDDI_VERSION >= NTDDI_WIN7)
    g_Monitor2.pfnNotifyUsedPorts   = Mon_NotifyUsedPorts;
    g_Monitor2.pfnNotifyUnusedPorts = Mon_NotifyUnusedPorts;
#endif

    *phMonitor = (HANDLE)1;
    return &g_Monitor2;
}

BOOL WINAPI DllMain(HINSTANCE hinst, DWORD reason, LPVOID reserved)
{
    (void)hinst; (void)reserved;
    if (reason == DLL_PROCESS_ATTACH) {
        LogLine("DllMain", "DLL_PROCESS_ATTACH");
    }
    return TRUE;
}
