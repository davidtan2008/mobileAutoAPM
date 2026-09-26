# mobileAutoAPM —— 统一入口
#
# 为什么需要 Makefile：
#   本仓库有三套测试（Python 工具 / rn-apm / ios-apm），此前**没有任何一条命令能全跑**。
#   「Agent 能否一条命令验证自己的改动」是 AI 友好度里权重最高的一条 ——
#   做不到的话，Agent 只能靠猜。所有命令都必须在**全新 clone** 上可跑。

.PHONY: help test test-py test-rn test-ios lint readiness gate check remediate build-portable verify-portable build-diagrams verify-diagrams demo-video doctor clean

PY      := python3
SCRIPTS := plugins/mobile-apm/scripts
TESTS   := plugins/mobile-apm/tests

help:  ## 列出所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ── 测试 ────────────────────────────────────────────────

test: test-py test-rn test-ios  ## 跑全部测试（提交前必跑）

test-py:  ## Python 工具测试（零依赖，最快）
	@for f in $(TESTS)/test_*.py; do \
		echo "── $$f"; \
		$(PY) -W error::ResourceWarning "$$f" || exit 1; \
	done

test-rn:  ## React Native SDK 测试
	@cd rn-apm && npm ci --no-audit --no-fund --silent && npm test

test-ios:  ## iOS SDK 测试
	@cd ios-apm && swift test

# ── 质量门禁 ────────────────────────────────────────────

lint:  ## Python 语法与风格检查
	@for f in $(SCRIPTS)/*.py tools/*.py; do $(PY) -m py_compile "$$f" || exit 1; done
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check .; \
	else \
		echo "  （未装 ruff，跳过风格检查）"; \
	fi

readiness:  ## 本仓库的 AI 友好度（CI 门禁同款检查）
	@$(PY) $(SCRIPTS)/ai_readiness.py --path .

MIN_SCORE ?= 80
gate:  ## 门禁：友好度低于 $(MIN_SCORE) 或有阻断项则非零退出（CI 与本地同款）
	@$(PY) $(SCRIPTS)/ai_readiness.py --path . --min-score $(MIN_SCORE)

remediate:  ## 支柱 A：扫描 → 改造 → 复扫（只生成计划，不写目标工程）
	@$(PY) $(SCRIPTS)/ai_remediate.py plan --path .

check: lint gate  ## 快速自检（跳过耗时测试）

# ── 构建 ────────────────────────────────────────────────

build-portable:  ## 由单一真源生成跨 Agent 可移植树（dist/）
	@$(PY) tools/build-portable.py

verify-portable:  ## 确认 dist/ 与源头一致（CI 门禁用）
	@tmp=$$(mktemp -d "$${TMPDIR:-/tmp}/mobileAutoAPM-dist.XXXXXX"); \
	trap 'rm -rf "$$tmp"' EXIT; \
	$(PY) tools/build-portable.py --target "$$tmp" >/dev/null; \
	if ! /usr/bin/diff -r "$$tmp" dist; then \
		echo "⛔ dist/ 与源头不一致 —— 请跑 make build-portable 后提交"; \
		exit 1; \
	fi

build-diagrams:  ## 由 SVG 渲染 PNG（双份提交：SVG 供网页，PNG 供 GitHub 兜底）
	@tools/build-diagrams.sh

verify-diagrams:  ## 确认 PNG 未落后于 SVG（CI 门禁用）
	@tools/build-diagrams.sh --check

# ── 发布素材 ────────────────────────────────────────────

DEMO_DIR   := docs/demo/translation-persistence
DEMO_MP4   := $(DEMO_DIR)/translation-persistence.mp4

demo-video:  ## 由真实命令输出渲染 60–90 秒 demo 视频（需 ffmpeg + rsvg-convert）
	@command -v ffmpeg >/dev/null || { echo "⛔ 缺少 ffmpeg"; exit 1; }
	@command -v rsvg-convert >/dev/null || { echo "⛔ 缺少 rsvg-convert"; exit 1; }
	@$(PY) tools/render_demo_slides.py --evidence $(DEMO_DIR) --out $(DEMO_DIR)/slides
	@for svg in $(DEMO_DIR)/slides/slide-*.svg; do \
		png="$${svg%.svg}.png"; \
		rsvg-convert -w 1920 -h 1080 "$$svg" -o "$$png" || exit 1; \
	done
	@tools/render_demo_video.sh $(DEMO_DIR)/slides $(DEMO_MP4)

# ── 环境 ────────────────────────────────────────────────

doctor:  ## 体检本机的移动端工具链（APM 任务开始前先跑）
	@$(PY) $(SCRIPTS)/apm_doctor.py

clean:  ## 清理构建产物
	@rm -rf rn-apm/dist ios-apm/.build
	@find . -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	@echo "已清理"
