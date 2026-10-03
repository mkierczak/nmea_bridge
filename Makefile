# Deploy to a Pico running MicroPython (needs `pip install mpremote`)
FILES = main.py NMEA.py l76x.py screens.py jamming.py sh1107.py writer.py roboto14.py roboto12.py \
        freesans11.py freesans20.py dogica_gps.py

.PHONY: deploy test
deploy:
	mpremote cp $(FILES) :

test:
	python3 -m pytest tests -q
