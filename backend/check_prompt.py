#!/usr/bin/env python
# -*- coding: utf-8 -*-
from database import SessionLocal
import models

db = SessionLocal()
try:
    config = db.query(models.PromptConfig).filter(
        models.PromptConfig.key == 'stage2_refine_shot'
    ).first()

    if config:
        print("=== Stage2 Prompt Template ===")
        print(config.content)
    else:
        print("Prompt not found!")
finally:
    db.close()
