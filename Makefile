# Deploy to a Raspberry Pi Pico W running MicroPython (needs `pip install mpremote`)
FILES = main.py NMEA.py l76x.py screens.py jamming.py spoofing.py wifi.py wificreds.py settings.py nav.py menu.py bridge.py ui.py linkcalc.py sh1107.py writer.py \
        roboto14.py version.py

# Modules that can be precompiled (everything except main.py); roboto14 is the font main.py imports
MPY_MODULES = NMEA l76x screens jamming spoofing wifi wificreds settings nav menu bridge ui linkcalc sh1107 writer roboto14

.PHONY: deploy deploy-mpy mpy test lint docs-check check check-clean version

# version.py is generated (and git-ignored): the git revision shown on the System page
version:
	@printf "VERSION = '%s'\n" "$$(git describe --always --dirty 2>/dev/null || echo dev)" > version.py

deploy: version
	mpremote cp $(FILES) :

# Precompiled modules need less RAM to load and boot faster. mpy-cross must match the firmware
# version (`pip install mpy-cross`, check `import sys; sys.implementation._mpy` on the board).
mpy:
	mkdir -p build
	for m in $(MPY_MODULES); do mpy-cross -o build/$$m.mpy $$m.py || exit 1; done

# A .py next to a .mpy on the board would shadow it, so remove the source copies first.
deploy-mpy: mpy version
	-for m in $(MPY_MODULES); do mpremote fs rm :$$m.py; done
	mpremote cp main.py version.py :
	mpremote cp build/*.mpy :

# Desktop checks (pip install -r requirements-dev.txt): tests, lint, docs links, MicroPython compile
test:
	python3 -m pytest -q

lint:
	ruff check .

docs-check:
	python3 tools/check_docs.py

# Everything CI runs on every push (see .github/workflows/test.yml)
check: test lint docs-check mpy
	mpy-cross -o build/main.mpy main.py

# `make check` inside a clean copy of the repository files (tracked plus untracked-but-not-ignored): catches
# anything that only works because of generated or ignored files in your working tree, as CI would see it
check-clean:
	rm -rf build/clean && mkdir -p build/clean
	git ls-files -co --exclude-standard | tar -cf - -T - | tar -xf - -C build/clean
	$(MAKE) -C build/clean check
