# epson-tmt70ii-escpos -- top-level Makefile
# SPDX-License-Identifier: GPL-2.0-or-later
#
# Quick start:
#   make selftest        # run the filter test suite (no printer needed)
#   make deb             # build the .deb (in ~/build, copied back here)
#   sudo make install    # install straight into the running system (no .deb)
#   make queue           # create the CUPS queue
#   make print-test      # send a test receipt
#
# Everything is overridable, e.g.:  sudo make install DESTDIR=/tmp/stage PREFIX=/usr

SHELL        := /bin/bash
PACKAGE      := epson-tmt70ii-escpos
VERSION      := 1.0.0

PYTHON       ?= python3
PREFIX       ?= /usr
DESTDIR      ?=

FILTER_DIR   ?= $(PREFIX)/lib/cups/filter
BACKEND_DIR  ?= $(PREFIX)/lib/cups/backend
PPD_DIR      ?= $(PREFIX)/share/ppd/$(PACKAGE)
UDEV_DIR     ?= $(PREFIX)/lib/udev/rules.d
DOC_DIR      ?= $(PREFIX)/share/doc/$(PACKAGE)

# CUPS queue helpers
PRINTER      ?= TM-T70II
DEVICE_URI   ?= tmt70://04b8:0202
PPD_INSTALLED := $(PPD_DIR)/EPSON_TM-T70II.ppd
LP_OPTS      ?=
FILE         ?=

INSTALL      ?= install
RM           ?= rm -f

.DEFAULT_GOAL := help
.PHONY: help all selftest check lint ppdtest compile \
        deb deb-lint install uninstall reinstall \
        queue remove-queue print-test dryrun decode capture-decode \
        clean distclean

## help: show this list of targets
help:
	@echo "$(PACKAGE) $(VERSION) -- make targets:"
	@sed -n 's/^## //p' $(MAKEFILE_LIST) | awk -F': ' '{printf "  \033[1m%-16s\033[0m %s\n",$$1,$$2}'

## all: alias for selftest
all: selftest

# ------------------------------------------------------------------ tests --
## compile: byte-compile the filter and backend
compile:
	$(PYTHON) -m py_compile src/filter/rastertoescpos src/backend/tmt70 tools/*.py

## selftest: raster -> filter -> ESC/POS end-to-end assertions (no printer)
selftest: compile
	PY=$(PYTHON) ./tools/selftest.sh

## ppdtest: validate the PPD with cupstestppd
ppdtest:
	@command -v cupstestppd >/dev/null || { echo "cupstestppd not found (install cups-client)"; exit 1; }
	cupstestppd -v ppd/EPSON_TM-T70II.ppd

## lint: compile + ppdtest + shellcheck (if available)
lint: compile ppdtest
	@command -v shellcheck >/dev/null && shellcheck tools/*.sh || echo "shellcheck not installed - skipped"

check: selftest ppdtest

# -------------------------------------------------------------- packaging --
## deb: build the Debian package (delegates to tools/build.sh)
deb:
	./tools/build.sh

## deb-lint: build the .deb and run lintian on it
deb-lint:
	./tools/build.sh lintian

# --------------------------------------------------------- system install --
## install: install filter/backend/PPD/udev into DESTDIR (default: live system)
install:
	$(INSTALL) -D -m0755 src/filter/rastertoescpos $(DESTDIR)$(FILTER_DIR)/rastertoescpos
	$(INSTALL) -D -m0755 src/backend/tmt70         $(DESTDIR)$(BACKEND_DIR)/tmt70
	$(INSTALL) -D -m0644 ppd/EPSON_TM-T70II.ppd    $(DESTDIR)$(PPD_DIR)/EPSON_TM-T70II.ppd
	$(INSTALL) -D -m0644 udev/60-epson-tmt70ii.rules $(DESTDIR)$(UDEV_DIR)/60-epson-tmt70ii.rules
	$(INSTALL) -D -m0644 docs/README.md            $(DESTDIR)$(DOC_DIR)/README.md
	$(INSTALL) -D -m0644 docs/INSTALLATION.md      $(DESTDIR)$(DOC_DIR)/INSTALLATION.md
	$(INSTALL) -D -m0644 docs/DEVELOPMENT.md       $(DESTDIR)$(DOC_DIR)/DEVELOPMENT.md
	$(INSTALL) -D -m0644 docs/NETWORK.md           $(DESTDIR)$(DOC_DIR)/NETWORK.md
	$(INSTALL) -D -m0644 docs/PROTOCOL.md          $(DESTDIR)$(DOC_DIR)/PROTOCOL.md
	@if [ -z "$(DESTDIR)" ]; then \
		udevadm control --reload-rules 2>/dev/null || true; \
		udevadm trigger --subsystem-match=usb --attr-match=idVendor=04b8 2>/dev/null || true; \
		(invoke-rc.d cups reload || systemctl reload-or-restart cups || service cups reload) 2>/dev/null || true; \
		echo "installed. add yourself to 'lp': sudo usermod -aG lp $$USER (then re-login)"; \
	fi

## uninstall: remove what 'install' put on the system
uninstall:
	$(RM) $(DESTDIR)$(FILTER_DIR)/rastertoescpos
	$(RM) $(DESTDIR)$(BACKEND_DIR)/tmt70
	$(RM) $(DESTDIR)$(PPD_DIR)/EPSON_TM-T70II.ppd
	$(RM) $(DESTDIR)$(UDEV_DIR)/60-epson-tmt70ii.rules
	-rmdir $(DESTDIR)$(PPD_DIR) 2>/dev/null || true
	@if [ -z "$(DESTDIR)" ]; then udevadm control --reload-rules 2>/dev/null || true; fi

reinstall: uninstall install

# ----------------------------------------------------------- CUPS queue ----
## queue: create the CUPS print queue ($(PRINTER) -> $(DEVICE_URI))
queue:
	lpadmin -p $(PRINTER) -E -v "$(DEVICE_URI)" -P "$(PPD_INSTALLED)"
	@echo "queue '$(PRINTER)' ready. options: lpoptions -p $(PRINTER) -l"

## remove-queue: delete the CUPS print queue
remove-queue:
	lpadmin -x $(PRINTER)

## net-queue: create a queue for a TM-T70II on the LAN (Ethernet UB-E04 / print server) -- HOST=ip
net-queue:
	@test -n "$(HOST)" || { echo "usage: make net-queue HOST=<printer-ip> [PORT=9100]"; exit 1; }
	lpadmin -p $(PRINTER) -E -v "socket://$(HOST):$(or $(PORT),9100)" -P "$(PPD_INSTALLED)"
	@echo "queue '$(PRINTER)' -> socket://$(HOST):$(or $(PORT),9100)"

## share: publish this host's queue to the LAN over IPP (run on the Linux host)
share:
	sudo cupsctl --share-printers --remote-any 2>/dev/null || sudo cupsctl --share-printers
	@sleep 2
	sudo lpadmin -p $(PRINTER) -o printer-is-shared=true
	@echo "shared. remote clients:  make client-queue SERVER=$$(hostname -f 2>/dev/null || hostname)"
	@echo "open the firewall if enabled:  sudo ufw allow 631/tcp ; sudo ufw allow mdns"

## unshare: stop publishing this host's queue
unshare:
	-sudo lpadmin -p $(PRINTER) -o printer-is-shared=false
	sudo cupsctl --no-share-printers

## client-queue: (on a REMOTE machine) add the shared queue -- SERVER=host [DRIVERLESS=1]
client-queue:
	@test -n "$(SERVER)" || { echo "usage: make client-queue SERVER=<host-or-ip> [DRIVERLESS=1]"; exit 1; }
	@if [ -n "$(DRIVERLESS)" ]; then \
	  lpadmin -p $(PRINTER) -E -v "ipp://$(SERVER)/printers/$(PRINTER)" -m everywhere; \
	elif [ -f "$(PPD_INSTALLED)" ]; then \
	  lpadmin -p $(PRINTER) -E -v "ipp://$(SERVER)/printers/$(PRINTER)" -P "$(PPD_INSTALLED)"; \
	else \
	  lpadmin -p $(PRINTER) -E -v "ipp://$(SERVER)/printers/$(PRINTER)" -m everywhere; \
	fi
	@echo "added '$(PRINTER)' pointing at ipp://$(SERVER)/printers/$(PRINTER)"

## print-test: send a generated test receipt to $(PRINTER)  (LP_OPTS=...)
print-test:
	@tmp=$$(mktemp /tmp/tmt70XXXX.txt); { \
	  echo "==== TM-T70II TEST RECEIPT ===="; date; \
	  echo; printf 'ITEM              QTY    PRICE\n'; \
	  printf 'Coffee              2     7.00\n'; \
	  printf 'Bagel               1     3.25\n'; \
	  printf -- '------------------------------\n'; \
	  printf 'TOTAL                    10.25\n'; \
	  echo; echo "the quick brown fox 0123456789"; \
	  echo "================================"; \
	} > $$tmp; \
	echo "lp -d $(PRINTER) $(LP_OPTS) $$tmp"; \
	lp -d $(PRINTER) $(LP_OPTS) $$tmp; \
	rm -f $$tmp

## dryrun: like print-test but capture the ESC/POS to /tmp and disassemble it
dryrun:
	@tmp=$$(mktemp /tmp/tmt70XXXX.txt); out=/tmp/tmt70-dryrun.bin; \
	{ echo "DRYRUN $(LP_OPTS)"; date; echo "the quick brown fox 0123456789"; } > $$tmp; \
	TMT70_DRYRUN=$$out lp -d $(PRINTER) $(LP_OPTS) $$tmp; \
	sleep 1; rm -f $$tmp; \
	echo "--- $$out ---"; $(PYTHON) tools/escpos_dis.py $$out

## decode: disassemble an ESC/POS / .prn stream   (make decode FILE=capture/x.prn)
decode:
	@test -n "$(FILE)" || { echo "usage: make decode FILE=path"; exit 1; }
	$(PYTHON) tools/escpos_dis.py $(FILE)

## capture-decode: disassemble every capture/*.prn
capture-decode:
	@for f in capture/*.prn; do echo "########## $$f"; $(PYTHON) tools/escpos_dis.py "$$f"; done

# --------------------------------------------------------------- cleanup --
## clean: remove build products and caches
clean:
	$(RM) -r build obj-* debian/.debhelper debian/$(PACKAGE) debian/files \
	         debian/debhelper-build-stamp
	find . -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
	$(RM) *.deb *.buildinfo *.changes

## distclean: clean + drop captured .prn streams
distclean: clean
	$(RM) -r capture
