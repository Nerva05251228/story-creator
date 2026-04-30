#!/usr/bin/env python
# -*- coding: utf-8 -*-

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal
import models

def verify_prompt():
    db = SessionLocal()
    try:
        config = db.query(models.PromptConfig).filter(
            models.PromptConfig.key == "stage2_refine_shot"
        ).first()

        if not config:
            print("[ERROR] Could not find stage2_refine_shot config")
            return

        print("=" * 80)
        print("Database Stage2 Prompt Content:")
        print("=" * 80)
        print(config.content)
        print("\n" + "=" * 80)

        # Test formatting
        try:
            test_result = config.content.format(
                total_shots=5,
                full_storyboard_json='{"test": "data"}'
            )
            print("[OK] Prompt formatting succeeded!")
            print("\nFormatted output preview:")
            print(test_result[:500])
        except KeyError as e:
            print(f"[ERROR] KeyError during formatting: {e}")
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    verify_prompt()
