"""
Update prompt configs in database to the defaults defined in main.py.
Usage: python update_prompts.py
"""
from __future__ import annotations

import ast
from pathlib import Path

from database import SessionLocal
import models

OBSOLETE_PROMPT_KEYS = {"stage3_subject_prompts"}


def load_default_prompts() -> list[dict]:
    main_path = Path(__file__).with_name("main.py")
    source = main_path.read_text(encoding="utf-8")
    module = ast.parse(source)
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEFAULT_PROMPTS":
                    return ast.literal_eval(node.value)
    raise RuntimeError("DEFAULT_PROMPTS not found in main.py")


def update_prompts() -> None:
    """Update prompt configs in database"""
    db = SessionLocal()
    try:
        default_prompts = load_default_prompts()
        updated_count = 0

        for prompt_data in default_prompts:
            key = prompt_data.get("key")
            if not key:
                continue

            config = db.query(models.PromptConfig).filter(models.PromptConfig.key == key).first()
            if config:
                config.name = prompt_data.get("name", config.name)
                config.description = prompt_data.get("description", config.description)
                config.content = prompt_data.get("content", config.content)
                updated_count += 1
                print(f"[OK] Updated: {config.name} (key: {key})")
            else:
                db.add(
                    models.PromptConfig(
                        key=key,
                        name=prompt_data.get("name", key),
                        description=prompt_data.get("description", ""),
                        content=prompt_data.get("content", ""),
                        is_active=True,
                    )
                )
                updated_count += 1
                print(f"[ADD] Created: {prompt_data.get('name', key)} (key: {key})")

        removed_count = 0
        for key in OBSOLETE_PROMPT_KEYS:
            config = db.query(models.PromptConfig).filter(models.PromptConfig.key == key).first()
            if config:
                db.delete(config)
                removed_count += 1
                print(f"[DEL] Removed: {config.name} (key: {key})")

        db.commit()
        print(f"\nTotal updated: {updated_count}, removed: {removed_count}")
        print("Please restart backend service to apply changes")

    except Exception as e:
        print(f"Update failed: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("Starting to update prompt configs...")
    update_prompts()
