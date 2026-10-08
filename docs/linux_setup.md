# Linux Setup: LXC Print Server for Windows Clients

Run the driver in an LXC container and let Windows map it as a network printer. All rendering happens on the Linux side, so Windows gets full output (images and graphics included), not the text-only limit of the `win/monitor` queue. It also avoids the WinUSB/Zadig/MSVCR120 problems, because Windows never touches the USB device.

The server side already exists in this repo: the `.deb`, `make share`, and [NETWORK.md](NETWORK.md). The LXC-specific work is getting the USB printer into the container. This guide assumes Proxmox; on plain LXC/Incus the same config lines apply, in a different file.

## 1. On the Proxmox host

**Container type.** Unprivileged is the default and safer, but needs one extra step for USB permissions (below). Debian 12 or Ubuntu 24.04 templates both meet the requirements.

**Pass the USB bus through.** Bind the whole `/dev/bus/usb` directory rather than one device path. The `001/005`-style path changes every time the printer is unplugged or power-cycled. Add this to `/etc/pve/lxc/<CTID>.conf`:

```
lxc.cgroup2.devices.allow: c 189:* rwm
lxc.mount.entry: /dev/bus/usb dev/bus/usb none bind,optional,create=dir
```

Avoid Proxmox's GUI "Device Passthrough" (`dev0:`) for this. It pins one specific path, which breaks on replug.

**Fix device permissions (unprivileged only).** udev doesn't run inside an unprivileged container, so the shipped rule `udev/60-epson-tmt70ii.rules` has no effect there. The rule has to live on the host. The `lp` group (gid 7) inside the container appears as gid 100007 on the host, so create `/etc/udev/rules.d/60-tmt70-lxc.rules` on the host:

```
ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="04b8", ATTR{idProduct}=="0202", MODE="0660", GROUP="100007"
```

Then run `udevadm control --reload-rules && udevadm trigger` and replug the printer. If you'd rather skip this, `MODE="0666"` also works, or use a privileged container, where the bind mount alone is enough.

**Other host setup:**

- Give the container a static IP or a DHCP reservation, since Windows maps it by address.
- Make sure nothing on the host itself (such as a host CUPS install) is holding the printer.

## 2. Inside the container

```
apt install ./epson-tmt70ii-escpos_1.0.0_all.deb cups-filters ghostscript poppler-utils
lsusb | grep 04b8          # should show 04b8:0202
lpinfo -v | grep tmt70     # should show tmt70://04b8:0202
make queue                 # or the lpadmin command in INSTALLATION.md §5
make share                 # cupsctl --share-printers --remote-any + printer-is-shared
cupsctl --remote-admin     # optional: web UI at http://<ip>:631 from the LAN
```

`cups-filters` and `ghostscript` are required here, not optional: Windows sends PDF, PWG raster or PostScript, and those are converted to raster before `rastertoescpos` runs.

The package's install script tolerates udev failing inside the container, so the install itself won't break.

**Set server-side defaults.** Windows' print dialog won't show Density, Cut and similar options, so set them as queue defaults:

```
lpadmin -p TM-T70II -o Density=dark -o EndOfJobCut=full -o BlankReduction=topbottom -o FeedBeforeCut=4mm
```

`BlankReduction` matters most: the default page is 80×297 mm, and without it short receipts print a long blank tail.

## 3. Mapping it in Windows

Go to **Settings → Printers & scanners → Add device → "The printer that I want isn't listed"**, then use either:

- **"Add a printer using an IP address or hostname"**: set Device type to *IPP Device* and enter `http://<lxc-ip>:631/printers/TM-T70II`. Windows 10/11 uses its built-in IPP Class Driver, so you don't install a driver.
- **"Select a shared printer by name"** with the same URL, choosing *Microsoft → MS Publisher Color Printer* as the driver. This sends PostScript, which Ghostscript on the server handles. It's the usual fallback if the IPP class driver acts up.

You then have a normal Windows printer that prints from any app.

## Not yet tested

- **Windows over IPP.** CUPS sharing from Linux matches the existing docs, but the Windows client path hasn't been tried against this queue.
- **Paper size.** In Windows, pick the 80 mm size when printing. If Windows sends Letter/A4, CUPS scales the page down to 80 mm width and the text comes out tiny. If that happens, set 80 mm as the default size in the Windows printer's preferences.
- **Unplug/replug inside the LXC.** The `tmt70` backend lists USB devices fresh on every job (via libusb), so replugging should be fine with the whole-bus bind mount. Confirm it once by unplugging, replugging, then printing.
