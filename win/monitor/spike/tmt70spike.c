/*
 * tmt70spike.c -- Phase 0 spike: minimal Windows Print Monitor (Monitor2).
 *
 * PURPOSE: answer one question before any real pipeline work is built --
 * will spoolsv.exe load an unsigned, locally-registered port monitor DLL
 * at all under current Windows security policy?  This DLL does nothing
 * useful: it exposes exactly one fixed port, "TMT70SPIKE:", and every
 * WritePort() call just appends the bytes it received to a log file.  If
 * a real print job's bytes show up in that log, the architecture in
 * ../../../docs -- er, in the plan at C:\Users\Quynn\.claude\plans --
 * is viable and Phase 1 (a real tmt70print-backed monitor) can proceed.
 *
 * This is throwaway code: it is NOT staged by tools/build-win.ps1 and
 * NOT shipped by tmt70print.iss.  See register-spike.ps1 for install/
 * uninstall and NOTES.md for findings.
 *
 * Reference: the exact MONITOR2 / MONITORINIT layouts came from the
 * MinGW-w64-bundled <winsplp.h> (mingw64/x86_64-w64-mingw32/include/
 * winsplp.h), not from memory -- getting this vtable wrong crashes
 * spoolsv.exe, a shared system service.
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

static const WCHAR PORT_NAME[]    = L"TMT70SPIKE:";
static const WCHAR MONITOR_DESC[] = L"TMT70 Spike Monitor";
static const WCHAR PORT_DESC[]    = L"Local Port (spike)";

/* ------------------------------------------------------------------ */
/* Logging -- every callback logs a line so NOTES.md can be filled in  */
/* from what actually happened, not guesswork.                         */
/* ------------------------------------------------------------------ */

/*
 * The real per-job port I/O may run inside an isolated PrintIsolationHost.exe
 * rather than spoolsv.exe itself (Windows' driver-isolation architecture), and
 * that context can have a different/restricted view of %ProgramData%. So:
 *   1. Log to a hardcoded, universally-writable path (no env-var lookup that
 *      could resolve differently in an isolated/AppContainer context).
 *   2. ALSO always write to the Application event log via ReportEventW, which
 *      is reachable from essentially any spooler-related process regardless
 *      of filesystem sandboxing -- so a callback that fires but can't touch
 *      the log file is still visible somewhere, instead of failing silently.
 * Never let a logging failure become a spooler-visible failure: every path
 * below swallows its own errors after trying the fallback.
 */
static const wchar_t *LOG_PATH = L"C:\\Windows\\Temp\\tmt70spike.log";

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

    char line[512];
    _snprintf(line, sizeof line, "[%s] %-24s %s", ts, tag, msg ? msg : "");
    line[sizeof line - 1] = 0;

    FILE *f = _wfopen(LOG_PATH, L"a");
    if (f) {
        fprintf(f, "%s\n", line);
        fclose(f);
    }

    wchar_t wline[512];
    MultiByteToWideChar(CP_ACP, 0, line, -1, wline, 512);
    ReportToEventLog(wline);
}

static void LogBytes(const char *tag, const BYTE *data, DWORD len)
{
    time_t now = time(NULL);
    struct tm tmv;
    localtime_s(&tmv, &now);
    char ts[32];
    strftime(ts, sizeof ts, "%Y-%m-%d %H:%M:%S", &tmv);

    char line[2048];
    int off = _snprintf(line, sizeof line, "[%s] %-24s len=%lu bytes=",
                         ts, tag, (unsigned long)len);
    DWORD show = len < 256 ? len : 256;
    for (DWORD i = 0; i < show && off < (int)sizeof(line) - 3; i++) {
        off += _snprintf(line + off, sizeof(line) - off, "%02X", data ? data[i] : 0);
    }
    if (len > show && off < (int)sizeof(line) - 20) {
        off += _snprintf(line + off, sizeof(line) - off, "...(+%lu more)",
                          (unsigned long)(len - show));
    }
    line[sizeof line - 1] = 0;

    FILE *f = _wfopen(LOG_PATH, L"a");
    if (f) {
        fprintf(f, "%s\n", line);
        fclose(f);
    }

    wchar_t wline[2048];
    MultiByteToWideChar(CP_ACP, 0, line, -1, wline, 2048);
    ReportToEventLog(wline);
}

/* ------------------------------------------------------------------ */
/* Monitor2 callbacks -- one fixed port, no persistent per-port state. */
/* ------------------------------------------------------------------ */

static WINBOOL WINAPI Spike_EnumPorts(HANDLE hMonitor, LPWSTR pName, DWORD Level,
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
    LogLine("EnumPorts", "reported TMT70SPIKE:");
    return TRUE;
}

static WINBOOL WINAPI Spike_OpenPort(HANDLE hMonitor, LPWSTR pName, PHANDLE pHandle)
{
    (void)hMonitor; (void)pName;
    LogLine("OpenPort", "opened");
    *pHandle = (HANDLE)1;   /* no real per-port state to track */
    return TRUE;
}

static WINBOOL WINAPI Spike_OpenPortEx(HANDLE hMonitor, HANDLE hMonitorPort,
                                        LPWSTR pPortName, LPWSTR pPrinterName,
                                        PHANDLE pHandle, PMONITOR2 pMonitor2)
{
    (void)hMonitor; (void)hMonitorPort; (void)pPortName; (void)pPrinterName; (void)pMonitor2;
    LogLine("OpenPortEx", "opened");
    *pHandle = (HANDLE)1;
    return TRUE;
}

static WINBOOL WINAPI Spike_StartDocPort(HANDLE hPort, LPWSTR pPrinterName,
                                          DWORD JobId, DWORD Level, LPBYTE pDocInfo)
{
    (void)hPort; (void)pPrinterName; (void)Level; (void)pDocInfo;
    char msg[64];
    _snprintf(msg, sizeof msg, "job=%lu", (unsigned long)JobId);
    msg[sizeof msg - 1] = 0;
    LogLine("StartDocPort", msg);
    return TRUE;
}

static WINBOOL WINAPI Spike_WritePort(HANDLE hPort, LPBYTE pBuffer, DWORD cbBuf,
                                       LPDWORD pcbWritten)
{
    (void)hPort;
    LogBytes("WritePort", pBuffer, cbBuf);
    *pcbWritten = cbBuf;
    return TRUE;
}

static WINBOOL WINAPI Spike_ReadPort(HANDLE hPort, LPBYTE pBuffer, DWORD cbBuffer,
                                      LPDWORD pcbRead)
{
    (void)hPort; (void)pBuffer; (void)cbBuffer;
    *pcbRead = 0;   /* no bidi data */
    return TRUE;
}

static WINBOOL WINAPI Spike_EndDocPort(HANDLE hPort)
{
    (void)hPort;
    LogLine("EndDocPort", "job ended");
    return TRUE;
}

static WINBOOL WINAPI Spike_ClosePort(HANDLE hPort)
{
    (void)hPort;
    LogLine("ClosePort", "closed");
    return TRUE;
}

static WINBOOL WINAPI Spike_AddPort(HANDLE hMonitor, LPWSTR pName, HWND hWnd,
                                     LPWSTR pMonitorName)
{
    (void)hMonitor; (void)pName; (void)hWnd; (void)pMonitorName;
    SetLastError(ERROR_NOT_SUPPORTED);   /* fixed single port; nothing to add */
    return FALSE;
}

static WINBOOL WINAPI Spike_AddPortEx(HANDLE hMonitor, LPWSTR pName, DWORD Level,
                                       LPBYTE lpBuffer, LPWSTR lpMonitorName)
{
    (void)hMonitor; (void)pName; (void)Level; (void)lpBuffer; (void)lpMonitorName;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

static WINBOOL WINAPI Spike_ConfigurePort(HANDLE hMonitor, LPWSTR pName, HWND hWnd,
                                           LPWSTR pPortName)
{
    (void)hMonitor; (void)pName; (void)pPortName;
    if (hWnd) {
        MessageBoxW(hWnd, L"TMT70 Spike Monitor has no configurable settings.",
                    L"TMT70 Spike Monitor", MB_OK | MB_ICONINFORMATION);
    }
    return TRUE;
}

static WINBOOL WINAPI Spike_DeletePort(HANDLE hMonitor, LPWSTR pName, HWND hWnd,
                                        LPWSTR pPortName)
{
    (void)hMonitor; (void)pName; (void)hWnd; (void)pPortName;
    return TRUE;   /* fixed port; nothing to actually remove */
}

static WINBOOL WINAPI Spike_GetPrinterDataFromPort(HANDLE hPort, DWORD ControlID,
    LPWSTR pValueName, LPWSTR lpInBuffer, DWORD cbInBuffer,
    LPWSTR lpOutBuffer, DWORD cbOutBuffer, LPDWORD lpcbReturned)
{
    (void)hPort; (void)ControlID; (void)pValueName; (void)lpInBuffer; (void)cbInBuffer;
    (void)lpOutBuffer; (void)cbOutBuffer;
    *lpcbReturned = 0;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

static WINBOOL WINAPI Spike_SetPortTimeOuts(HANDLE hPort, LPCOMMTIMEOUTS lpCTO,
                                             DWORD reserved)
{
    (void)hPort; (void)lpCTO; (void)reserved;
    return TRUE;   /* accept and ignore */
}

static WINBOOL WINAPI Spike_XcvOpenPort(HANDLE hMonitor, LPCWSTR pszObject,
                                         ACCESS_MASK GrantedAccess, PHANDLE phXcv)
{
    (void)hMonitor; (void)pszObject; (void)GrantedAccess;
    *phXcv = NULL;
    SetLastError(ERROR_NOT_SUPPORTED);
    return FALSE;
}

static DWORD WINAPI Spike_XcvDataPort(HANDLE hXcv, LPCWSTR pszDataName,
    PBYTE pInputData, DWORD cbInputData, PBYTE pOutputData, DWORD cbOutputData,
    PDWORD pcbOutputNeeded)
{
    (void)hXcv; (void)pszDataName; (void)pInputData; (void)cbInputData;
    (void)pOutputData; (void)cbOutputData;
    if (pcbOutputNeeded) *pcbOutputNeeded = 0;
    return ERROR_NOT_SUPPORTED;
}

static WINBOOL WINAPI Spike_XcvClosePort(HANDLE hXcv)
{
    (void)hXcv;
    return TRUE;
}

static VOID WINAPI Spike_Shutdown(HANDLE hMonitor)
{
    (void)hMonitor;
    LogLine("Shutdown", "spooler is unloading the monitor");
}

#if (NTDDI_VERSION >= NTDDI_WINXP)
static DWORD WINAPI Spike_SendRecvBidiDataFromPort(HANDLE hPort, DWORD dwAccessBit,
    LPCWSTR pAction, PBIDI_REQUEST_CONTAINER pReqData, PBIDI_RESPONSE_CONTAINER *ppResData)
{
    (void)hPort; (void)dwAccessBit; (void)pAction; (void)pReqData;
    if (ppResData) *ppResData = NULL;
    return ERROR_NOT_SUPPORTED;
}
#endif

#if (NTDDI_VERSION >= NTDDI_WIN7)
static DWORD WINAPI Spike_NotifyUsedPorts(HANDLE hMonitor, DWORD cPorts, PCWSTR *ppszPorts)
{
    (void)hMonitor; (void)cPorts; (void)ppszPorts;
    return NO_ERROR;
}

static DWORD WINAPI Spike_NotifyUnusedPorts(HANDLE hMonitor, DWORD cPorts, PCWSTR *ppszPorts)
{
    (void)hMonitor; (void)cPorts; (void)ppszPorts;
    return NO_ERROR;
}
#endif

/* ------------------------------------------------------------------ */
/* Entry point -- this is the one export spoolsv.exe looks for.        */
/* ------------------------------------------------------------------ */

static MONITOR2 g_Monitor2;

__declspec(dllexport)
LPMONITOR2 WINAPI InitializePrintMonitor2(PMONITORINIT pMonitorInit, PHANDLE phMonitor)
{
    (void)pMonitorInit;
    LogLine("InitializePrintMonitor2", "spooler is initializing the monitor");

    ZeroMemory(&g_Monitor2, sizeof(g_Monitor2));
    g_Monitor2.cbSize                   = sizeof(g_Monitor2);
    g_Monitor2.pfnEnumPorts              = Spike_EnumPorts;
    g_Monitor2.pfnOpenPort               = Spike_OpenPort;
    g_Monitor2.pfnOpenPortEx             = Spike_OpenPortEx;
    g_Monitor2.pfnStartDocPort           = Spike_StartDocPort;
    g_Monitor2.pfnWritePort              = Spike_WritePort;
    g_Monitor2.pfnReadPort               = Spike_ReadPort;
    g_Monitor2.pfnEndDocPort             = Spike_EndDocPort;
    g_Monitor2.pfnClosePort              = Spike_ClosePort;
    g_Monitor2.pfnAddPort                = Spike_AddPort;
    g_Monitor2.pfnAddPortEx              = Spike_AddPortEx;
    g_Monitor2.pfnConfigurePort          = Spike_ConfigurePort;
    g_Monitor2.pfnDeletePort             = Spike_DeletePort;
    g_Monitor2.pfnGetPrinterDataFromPort = Spike_GetPrinterDataFromPort;
    g_Monitor2.pfnSetPortTimeOuts        = Spike_SetPortTimeOuts;
    g_Monitor2.pfnXcvOpenPort            = Spike_XcvOpenPort;
    g_Monitor2.pfnXcvDataPort            = Spike_XcvDataPort;
    g_Monitor2.pfnXcvClosePort           = Spike_XcvClosePort;
    g_Monitor2.pfnShutdown               = Spike_Shutdown;
#if (NTDDI_VERSION >= NTDDI_WINXP)
    g_Monitor2.pfnSendRecvBidiDataFromPort = Spike_SendRecvBidiDataFromPort;
#endif
#if (NTDDI_VERSION >= NTDDI_WIN7)
    g_Monitor2.pfnNotifyUsedPorts   = Spike_NotifyUsedPorts;
    g_Monitor2.pfnNotifyUnusedPorts = Spike_NotifyUnusedPorts;
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
