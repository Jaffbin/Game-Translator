# AutoGame Localizer UI Integration v13

## 本轮目标
把 Localization IDE 的 Translation Editor 从“单条编辑”推进到“高效率审校工作区”，同时保持现有翻译核心和 Patch 生命周期稳定。

## v13 changes
- P4 Entries 支持当前页多选。
- 批量 `Review` 使用新的 `/api/entries/bulk-update`。
- 批量 `Lock` 使用新的 `/api/entries/bulk-update`，执行前二次确认。
- 新增 `/api/qa/issues`，从当前项目的 `error` / `needs_review` entries 生成可定位的问题列表。
- QA panel 支持点击问题直接回到对应 Entry。
- `Ctrl + Shift + Q`：在非输入控件状态下跳到下一个 QA 问题。
- Translation Editor 左侧加入选择状态、选中数量和批量操作栏。
- QA / Editor / bulk actions 保持现有危险操作与锁定语义。

## Backend compatibility
- `/api/entries`, `/api/entries/update`, `/api/tasks`, `/api/glossary`, `/api/translation-memory`, `/api/entries/suggest` 保持原契约。
- `/api/entries/bulk-update` 只允许 `reviewed` / `lock` / `unlock`。
- `/api/qa/issues` 只暴露 Entry 可审校所需字段，不暴露 API Key 或绝对项目路径。

## Regression
- Existing pytest suite: PASS
- v13 feature tests: PASS
- Embedded JavaScript syntax: PASS
- Browser runtime: PASS
- Responsive: 1000x700 PASS / 1220x860 PASS

## Files
- app_main.py
- ui_integration.py
- ui_pages.py
- phase75_web.py
- test_v13_features.py
- UI_INTEGRATION_V13.md

旧的 phase UI 文件和现有 `agl/` 核心实现继续保留，不要在这一步删除。
