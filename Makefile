SNAPSHOT_DST ?= ./snapshot
REPO_URL ?= https://github.com/AssociationInfoclimat/data-platform.git
REPO_BRANCH ?= main

.PHONY: sync-snapshot run test

# Clone / met à jour le snapshot du corpus PUBLIC data-platform dans $(SNAPSHOT_DST),
# via le même mécanisme `gitsync` qu'en prod (clone anonyme, fetch+reset). Pour le bot
# local : pointer DATAPLATFORM_SNAPSHOT_DIR sur $(SNAPSHOT_DST).
sync-snapshot:
	CLONE_DIR=$(SNAPSHOT_DST) REPO_URL=$(REPO_URL) REPO_BRANCH=$(REPO_BRANCH) \
		uv run python -m ic_data_bot.gitsync
	@echo "Snapshot synchronisé dans $(SNAPSHOT_DST)"

run:
	uv run ic-data-bot

test:
	uv run pytest -v
