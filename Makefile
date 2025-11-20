REGISTRY ?= ghcr.io/example
WATCHER_IMAGE ?= $(REGISTRY)/project-fyr-watcher
ANALYZER_IMAGE ?= $(REGISTRY)/project-fyr-analyzer
TAG ?= latest
PLATFORM ?= linux/amd64
BUILDX_FLAGS ?= --load

.PHONY: help \
	build-watcher build-analyzer \
	buildx-watcher buildx-analyzer \
	push-watcher push-analyzer

help:
	@echo "Available targets:"
	@echo "  build-watcher        Build local watcher image (Dockerfile)"
	@echo "  build-analyzer       Build local analyzer image (Dockerfile.analyzer)"
	@echo "  buildx-watcher       Build watcher image for PLATFORM using buildx ($(PLATFORM))"
	@echo "  buildx-analyzer      Build analyzer image for PLATFORM using buildx ($(PLATFORM))"
	@echo "  push-watcher         Build & push watcher image for PLATFORM"
	@echo "  push-analyzer        Build & push analyzer image for PLATFORM"

build-watcher:
	docker build -f Dockerfile -t $(WATCHER_IMAGE):$(TAG) .

build-analyzer:
	docker build -f Dockerfile.analyzer -t $(ANALYZER_IMAGE):$(TAG) .

buildx-watcher:
	docker buildx build --platform $(PLATFORM) -f Dockerfile \
		-t $(WATCHER_IMAGE):$(TAG) $(BUILDX_FLAGS) .

buildx-analyzer:
	docker buildx build --platform $(PLATFORM) -f Dockerfile.analyzer \
		-t $(ANALYZER_IMAGE):$(TAG) $(BUILDX_FLAGS) .

push-watcher:
	docker buildx build --platform $(PLATFORM) -f Dockerfile \
		-t $(WATCHER_IMAGE):$(TAG) --push .

push-analyzer:
	docker buildx build --platform $(PLATFORM) -f Dockerfile.analyzer \
		-t $(ANALYZER_IMAGE):$(TAG) --push .
