# Prop Card Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `道具` as a first-class subject card type across prompt generation, subject management, storyboard selection, and reference-image pipelines while preserving existing `声音` special cases.

**Architecture:** Extend the existing “main subject types” boundary from `角色/场景` to `角色/场景/道具`, but keep `声音` as a separate attached card path. Update backend prompt/config normalization first, then wire the frontend four-column subject UI and selection panels to the new type, while keeping scene-only logic explicit.

**Tech Stack:** FastAPI, SQLAlchemy ORM, SQLite, vanilla JS frontend, unittest, Node-based JS assertions

---

### Task 1: Expand subject type constants and backend normalization

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_backend.py`

- [ ] **Step 1: Write the failing test**

```python
def test_prop_card_type_is_accepted_by_subject_normalization(self):
    subject = {
        "name": "青铜匕首",
        "type": "道具",
        "alias": "带血旧匕首",
        "ai_prompt": "青铜材质，刀刃有磨损，木柄开裂",
        "role_personality": "should be cleared",
    }

    normalized = main._normalize_subject_detail_entry(subject)

    self.assertIsNotNone(normalized)
    self.assertEqual(normalized["type"], "道具")
    self.assertEqual(normalized["role_personality"], "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend.PropCardBackendTests.test_prop_card_type_is_accepted_by_subject_normalization`

Expected: FAIL because `道具` is rejected by `ALLOWED_CARD_TYPES`.

- [ ] **Step 3: Write minimal implementation**

Update constants and normalization boundaries in `backend/main.py`:

```python
ALLOWED_CARD_TYPES = ("角色", "场景", "道具")
ALL_SUBJECT_CARD_TYPES = ("角色", "场景", "道具", "声音")

...

return {
    "name": name,
    "type": subject_type,
    "alias": (alias or "").strip(),
    "ai_prompt": (ai_prompt or "").strip(),
    "role_personality": (role_personality or "").strip() if subject_type == "角色" else ""
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend.PropCardBackendTests.test_prop_card_type_is_accepted_by_subject_normalization`

Expected: PASS

- [ ] **Step 5: Commit**

This workspace is not a git repository. Skip commit and record the limitation in status notes.

### Task 2: Upgrade prompt/config content to support props

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_backend.py`

- [ ] **Step 1: Write the failing test**

```python
def test_stage2_prompt_upgrade_mentions_prop_card_type(self):
    upgraded = main.upgrade_stage2_refine_shot_prompt_content(
        "1. 主体类型只有两类：角色 / 场景。\n"
        "4. 为每个主体生成绘画提示词与别名。\n"
        "- 角色 ai_prompt：年龄 + 性别 + 表情 + 眼睛 + 发型 + 配饰 + 衣服 + 细节\n"
        "- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征\n"
        "\"type\": \"角色 或 场景\",\n"
        "\"role_personality\": \"角色性格（中文一句话），场景填空字符串\",\n"
    )

    self.assertIn("角色 / 场景 / 道具", upgraded)
    self.assertIn("道具 ai_prompt", upgraded)
    self.assertIn("道具填空字符串", upgraded)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend.PropCardBackendTests.test_stage2_prompt_upgrade_mentions_prop_card_type`

Expected: FAIL because current upgrader only knows role personality additions.

- [ ] **Step 3: Write minimal implementation**

Add/extend prompt upgrader helpers in `backend/main.py` so default built-in prompt text and safe upgrade logic both handle props:

```python
updated = updated.replace("主体类型只有两类：角色 / 场景。", "主体类型只有三类：角色 / 场景 / 道具。")
updated = updated.replace("\"type\": \"角色 或 场景\",", "\"type\": \"角色 或 场景 或 道具\",")
updated = updated.replace(
    "- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征",
    "- 场景 ai_prompt：整体风格、环境氛围、光影效果、细节特征\n"
    "     - 道具 ai_prompt：材质 + 造型 + 颜色 + 结构特征 + 使用痕迹 + 关键细节"
)
updated = updated.replace("场景填空字符串", "场景/道具填空字符串")
```

Also update the built-in prompt config seed strings for:

- detailed storyboard subject extraction
- `stage2_refine_shot`
- `generate_subject_ai_prompt`

- [ ] **Step 4: Run test to verify it passes**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend.PropCardBackendTests.test_stage2_prompt_upgrade_mentions_prop_card_type`

Expected: PASS

- [ ] **Step 5: Commit**

Skip; no git repo.

### Task 3: Extend subject CRUD and selection/reference backend flows

**Files:**
- Modify: `D:\text2image2video_20260310\backend\main.py`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_backend.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_collect_storyboard2_reference_images_includes_prop_cards(self):
    urls = main._collect_storyboard2_reference_images(
        storyboard2_shot=self.storyboard2_shot,
        db=self.db,
        include_scene_references=False,
    )
    self.assertIn("https://cdn.example.com/prop-ref.png", urls)

def test_extract_scene_description_ignores_prop_cards(self):
    description = main.extract_scene_description(self.source_shot, self.db)
    self.assertIn("废弃仓库", description)
    self.assertNotIn("青铜匕首", description)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend.PropCardBackendTests`

Expected: FAIL because current subject queries and sort logic only include role/scene, and some scene handling relies on `else`.

- [ ] **Step 3: Write minimal implementation**

Update backend query/filter/sort sites in `main.py` that currently use `ALLOWED_CARD_TYPES` or `else => scene` assumptions, especially:

- subject library card fetch
- stage2 card creation/update
- storyboard2 available subject payloads
- storyboard2 subject-name collection
- storyboard2 prompt application
- reference-image collection
- scene-only extraction helpers

Use explicit type checks:

```python
def _subject_type_sort_key(card_type: str) -> int:
    if card_type == "角色":
        return 0
    if card_type == "场景":
        return 1
    if card_type == "道具":
        return 2
    return 9
```

and:

```python
if card.card_type == "角色":
    ...
elif card.card_type == "场景":
    ...
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend.PropCardBackendTests`

Expected: PASS

- [ ] **Step 5: Commit**

Skip; no git repo.

### Task 4: Render prop cards in the subject page and sidebar editor

**Files:**
- Modify: `D:\text2image2video_20260310\frontend\js\app.js`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_frontend.js`

- [ ] **Step 1: Write the failing tests**

```javascript
assert.strictEqual(
  sandbox.getCardImageActionMode({ card_type: '道具' }),
  'image'
);

assert.strictEqual(
  sandbox.shouldShowThreeViewButton({ card_type: '道具' }),
  false
);

assert.strictEqual(
  sandbox.getPromptPlaceholderByCardType('道具'),
  '描述道具的外观与细节（例如：青铜材质的旧匕首，刀刃有磨损，木柄缠着发黑布条...）'
);
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node tests/test_prop_card_frontend.js`

Expected: FAIL because helper branches only know role/scene/sound.

- [ ] **Step 3: Write minimal implementation**

Refactor small helpers out of `frontend/js/app.js` and wire them into rendering:

```javascript
function shouldShowThreeViewButton(card) {
    return (card?.card_type || '').trim() === '角色';
}

function getPromptPlaceholderByCardType(cardType) {
    if (cardType === '角色') return '...';
    if (cardType === '场景') return '...';
    if (cardType === '道具') return '描述道具的外观与细节（例如：青铜材质的旧匕首，刀刃有磨损，木柄缠着发黑布条...）';
    return '';
}
```

Update subject page layout from 3 columns to 4 columns and render a `propsColumn`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `node tests/test_prop_card_frontend.js`

Expected: PASS

- [ ] **Step 5: Commit**

Skip; no git repo.

### Task 5: Add prop groups to storyboard and storyboard2 subject selectors

**Files:**
- Modify: `D:\text2image2video_20260310\frontend\js\app.js`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_frontend.js`

- [ ] **Step 1: Write the failing tests**

```javascript
const grouped = sandbox.groupStoryboardSubjectCards([
  { id: 1, card_type: '角色', name: '主角' },
  { id: 2, card_type: '场景', name: '仓库' },
  { id: 3, card_type: '道具', name: '匕首' },
  { id: 4, card_type: '声音', name: '旁白' },
]);

assert.deepStrictEqual(grouped.props.map(item => item.name), ['匕首']);
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node tests/test_prop_card_frontend.js`

Expected: FAIL because grouping logic only returns characters/scenes/sounds.

- [ ] **Step 3: Write minimal implementation**

Create shared grouping helper and update storyboard/storyboard2 selection UIs to render:

```javascript
{
  characters: cards.filter(card => card.card_type === '角色'),
  scenes: cards.filter(card => card.card_type === '场景'),
  props: cards.filter(card => card.card_type === '道具'),
  sounds: cards.filter(card => card.card_type === '声音'),
}
```

Ensure:

- props are rendered in `selected_card_ids`
- sounds stay in `selected_sound_card_ids`
- scene-only uploader card remains only in the scene group

- [ ] **Step 4: Run tests to verify they pass**

Run: `node tests/test_prop_card_frontend.js`

Expected: PASS

- [ ] **Step 5: Commit**

Skip; no git repo.

### Task 6: Verification sweep

**Files:**
- Test: `D:\text2image2video_20260310\tests\test_prop_card_backend.py`
- Test: `D:\text2image2video_20260310\tests\test_prop_card_frontend.js`
- Verify: `D:\text2image2video_20260310\backend\main.py`
- Verify: `D:\text2image2video_20260310\frontend\js\app.js`

- [ ] **Step 1: Run backend regression slice**

Run: `.\\venv\\Scripts\\python.exe -m unittest tests.test_prop_card_backend tests.test_three_view_image_generation`

Expected: PASS

- [ ] **Step 2: Run frontend regression slice**

Run: `node tests/test_prop_card_frontend.js && node tests/test_three_view_request.js`

Expected: PASS

- [ ] **Step 3: Run Python syntax verification**

Run: `.\\venv\\Scripts\\python.exe -m py_compile backend\\main.py`

Expected: no output

- [ ] **Step 4: Review spec coverage**

Confirm implementation touches:

- subject constants
- prompt upgrades
- CRUD/query filters
- four-column subject page
- storyboard selectors
- reference image carrying
- scene-only isolation

- [ ] **Step 5: Commit**

Skip; no git repo.
