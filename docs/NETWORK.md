# Network printing with the TM-T70II

The TM-T70II on your machine is **USB** (`04b8:0202`). There are two ways to get
it printing from other machines.

```
                        ┌─────────────────────────────────────────┐
   remote client  ──IPP──▶  Linux host (CUPS)  ──filter+backend──▶ TM-T70II (USB)
                        └─────────────────────────────────────────┘
```

---

## A. Share the Linux host's queue over the LAN (recommended)

The host does all the rendering (`rastertoescpos` + `tmt70` backend); clients
just submit jobs over IPP.

### On the Linux host (where the printer is plugged in)

```bash
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb   # or: sudo make install
make queue                     # USB queue: PRINTER=TM-T70II DEVICE_URI=tmt70://04b8:0202
make share                     # = cupsctl --share-printers --remote-any
                               #   + lpadmin -p TM-T70II -o printer-is-shared=true
```

If a firewall is active:

```bash
sudo ufw allow 631/tcp         # IPP
sudo ufw allow 5353/udp        # mDNS / Bonjour discovery (optional but nice)
```

CUPS now advertises `ipp://<host>:631/printers/TM-T70II` and (with Avahi
installed) a DNS-SD record the clients can auto-discover.

### On each remote machine

**Auto-discovery** (Linux with `cups-browsed`/Avahi, or macOS): the printer
just appears in the print dialog as `TM-T70II @ <host>`. Nothing to configure.

**Manual, full options** — install this package on the client too so the client
print dialog shows Density / Speed / Cut / etc., then point it at the server:

```bash
sudo apt install ./epson-tmt70ii-escpos_1.0.0_all.deb
make client-queue SERVER=<host-or-ip>          # uses the bundled PPD
lp -d TM-T70II -o Density=dark -o EndOfJobCut=full file.pdf
```

**Manual, driverless** — no package on the client; options limited to whatever
the server exports as IPP attributes:

```bash
make client-queue SERVER=<host-or-ip> DRIVERLESS=1     # lpadmin ... -m everywhere
# or straight CUPS:
lpadmin -p TM-T70II -E -v ipp://<host>/printers/TM-T70II -m everywhere
```

Windows clients: *Add Printer ▸ The printer that I want isn't listed ▸ Select a
shared printer by name ▸* `http://<host>:631/printers/TM-T70II`, driver
"Generic / MS Publisher Color Printer" or an IPP class driver.

---

## B. Printer on Ethernet instead of USB

If the unit is later fitted with an Ethernet interface (Epson UB-E04) or reached
through a serial-to-Ethernet adaptor, skip USB entirely — the `tmt70` backend
speaks raw TCP (port 9100 / JetDirect):

```bash
make net-queue HOST=192.168.1.50           # -> socket://192.168.1.50:9100
# equivalently:
lpadmin -p TM-T70II -E -v socket://192.168.1.50:9100 \
        -P /usr/share/ppd/epson-tmt70ii-escpos/EPSON_TM-T70II.ppd
```

All the same `-o` options apply; only the transport changes. This queue can also
be shared with `make share` for other clients.

---

## Quick checks

```bash
lpstat -p TM-T70II -l            # queue state + device URI
lpstat -e                        # queues visible to this client (incl. discovered)
lpoptions -p TM-T70II -l         # option list the client will show
driverless list ipp://<host>/    # what the server exports (driverless)
cupsctl | grep -i share          # is sharing on?
```

## Notes

* Rendering always happens on the host that owns the USB/socket queue, so the
  client needs no `libusb` access and no udev rule.
* `printer-is-shared=true` is per-queue; `cupsctl --share-printers` is the global
  switch. `make share` sets both.
* For a locked-down host, replace `--remote-any` with explicit
  `Allow`/`BrowseAllow` lines in `/etc/cups/cupsd.conf`.
