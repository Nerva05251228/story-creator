"""
导入1.xlsx初始数据到爆款库
"""
import sys
sys.path.insert(0, '.')

import pandas as pd
from database import SessionLocal
from models import HitDrama, HitDramaEditHistory
from datetime import datetime

def import_excel():
    db = SessionLocal()

    try:
        # 读取Excel
        df = pd.read_excel('../1.xlsx')
        print(f'读取到 {len(df)} 行数据')

        # 过滤空行
        df_clean = df.dropna(how='all')
        print(f'过滤后剩余 {len(df_clean)} 行有效数据')

        imported_count = 0
        for idx, row in df_clean.iterrows():
            # 跳过剧名为空的行
            if pd.isna(row['剧名']) or str(row['剧名']).strip() == '':
                continue

            drama = HitDrama(
                organizer=str(row['整理人']) if not pd.isna(row['整理人']) else '',
                drama_name=str(row['剧名']),
                view_count=str(row['播放量']) if not pd.isna(row['播放量']) else '',
                opening_15_sentences=str(row['开头15句']) if not pd.isna(row['开头15句']) else '',
                first_episode_script=str(row['第一集文案']) if not pd.isna(row['第一集文案']) else '',
                online_time=str(row['上线时间']) if not pd.isna(row['上线时间']) else '',
                created_by='admin'
            )
            db.add(drama)
            db.flush()

            # 记录创建历史
            history = HitDramaEditHistory(
                drama_id=drama.id,
                action_type='create',
                field_name=None,
                old_value=None,
                new_value=f'导入记录：{drama.drama_name}',
                edited_by='admin'
            )
            db.add(history)

            imported_count += 1
            print(f'导入第 {imported_count} 条：{drama.drama_name}')

        db.commit()
        print(f'\n导入完成！共导入 {imported_count} 条记录')

    except Exception as e:
        print(f'导入失败：{e}')
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()

if __name__ == '__main__':
    import_excel()
