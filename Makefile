# Deploy to a Raspberry Pi Pico W running MicroPython (needs `pip install mpremote`)
FILES = main.py NMEA.py l76x.py screens.py jamming.py spoofing.py wifi.py wificreds.py sh1107.py writer.py \
        roboto14.py roboto12.py freesans11.py freesans20.py dogica_gps.py

# Modules that can be precompiled (everything except main.py); roboto14 is the font main.py imports
MPY_MODULES = NMEA l76x screens jamming spoofing wifi wificreds sh1107 writer roboto14

.PHONY: deploy deploy-mpy mpy test
deploy:
	mpremote cp $(FILES) :

# Precompiled modules need less RAM to load and boot faster. mpy-cross must match the firmware
# version (`pip install mpy-cross`, check `import sys; sys.implementation._mpy` on the board).
mpy:
	mkdir -p build
	for m in $(MPY_MODULES); do mpy-cross -o build/$$m.mpy $$m.py || exit 1; done

# A .py next to a .mpy on the board would shadow it, so remove the source copies first.
deploy-mpy: mpy
	-for m in $(MPY_MODULES); do mpremote fs rm :$$m.py; done
	mpremote cp main.py :
	mpremote cp build/*.mpy :

test:
	python3 -m pytest tests -q
