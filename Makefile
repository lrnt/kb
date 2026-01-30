.PHONY: all clean serve dev habits deploy

all:
	./web/scripts/build.py --all

clean:
	rm -rf web/build

serve:
	python3 -m http.server -d web/build/ 8000

dev:
	./web/scripts/dev.py

habits:
	./habits/main.py

deploy: clean all
	netlify deploy --prod --dir=web/build/
