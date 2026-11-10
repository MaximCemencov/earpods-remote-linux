PREFIX   ?= $(HOME)/.local
LIBDIR   := $(PREFIX)/share/applemote
BINDIR   := $(PREFIX)/bin
UNITDIR  := $(HOME)/.config/systemd/user
UDEVRULE := /etc/udev/rules.d/70-applemote.rules

UINPUTRULE := /etc/udev/rules.d/71-applemote-uinput.rules

.PHONY: install install-uinput uninstall test

install:
	install -d $(LIBDIR)/applemote $(LIBDIR)/templates $(BINDIR) $(UNITDIR)
	install -m644 applemote/*.py $(LIBDIR)/applemote/
	install -m644 templates/*.npz $(LIBDIR)/templates/
	printf '#!/bin/sh\nPYTHONPATH="$(LIBDIR)$${PYTHONPATH:+:$$PYTHONPATH}" exec python3 -P -m applemote "$$@"\n' > $(BINDIR)/applemote
	chmod 755 $(BINDIR)/applemote
	sed 's|@BINDIR@|$(BINDIR)|' contrib/applemote.service > $(UNITDIR)/applemote.service
	sudo install -m644 contrib/70-applemote.rules $(UDEVRULE)
	sudo udevadm control --reload
	sudo udevadm trigger --subsystem-match=input --action=change
	systemctl --user daemon-reload
	@echo
	@echo "Installed. Next:  applemote setup   (then 'applemote calibrate' if setup asks for it)"
	@echo "                  systemctl --user enable --now applemote"

# Only needed when 'applemote setup' says keys must go through uinput.
install-uinput:
	sudo install -m644 contrib/71-applemote-uinput.rules $(UINPUTRULE)
	sudo udevadm control --reload
	sudo udevadm trigger --name-match=uinput --action=change
	@echo "Log out and back in, then: systemctl --user restart applemote"

uninstall:
	-systemctl --user disable --now applemote
	rm -rf $(LIBDIR) $(BINDIR)/applemote $(UNITDIR)/applemote.service
	sudo rm -f $(UDEVRULE) $(UINPUTRULE)
	sudo udevadm control --reload
	systemctl --user daemon-reload
	@echo "Config and calibration kept in ~/.config/applemote (remove by hand if unwanted)."

test:
	python3 -m unittest discover -s tests -v
