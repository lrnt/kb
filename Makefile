.PHONY: all clean serve dev habits deploy book-covers

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

book-covers:
	@for md in $$(find ../vault/books -name '*.md'); do \
		jpg="$${md%.md}.jpg"; \
		[ -f "$$jpg" ] && continue; \
		isbn=$$(awk -F: 'BEGIN{inside=0} /^---/{inside=!inside; next} inside && $$1 ~ /^isbn13$$/ {gsub(/[^0-9]/, "", $$2); print $$2; exit}' "$$md"); \
		[ -z "$$isbn" ] && echo "Missing ISBN13: $$md" && continue; \
		curl -fsL "https://covers.openlibrary.org/b/isbn/$${isbn}-L.jpg?default=false" -o "$$jpg" || (rm -f "$$jpg" && echo "No cover: $$md"); \
	done

deploy: clean all
	netlify deploy --prod --dir=web/build/
