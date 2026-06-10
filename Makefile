SNAPSHOT_SRC ?= ../site-infoclimat/data-platform
SNAPSHOT_DST ?= ./snapshot

.PHONY: sync-snapshot run test

sync-snapshot:
	rm -rf $(SNAPSHOT_DST)
	mkdir -p $(SNAPSHOT_DST)
	cp -R $(SNAPSHOT_SRC)/. $(SNAPSHOT_DST)/
	@mkdir -p $(SNAPSHOT_DST)/migration-data
	@cp ../site-infoclimat/migration-data/schema-directeur-data.md $(SNAPSHOT_DST)/migration-data/ 2>/dev/null || true
	@echo "Snapshot synchronisé dans $(SNAPSHOT_DST)"

run:
	uv run ic-data-bot

test:
	uv run pytest -v
