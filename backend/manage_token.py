import sys
import secrets
from sqlalchemy.orm import Session
from database import SessionLocal, engine
import models

# 创建数据库表
models.Base.metadata.create_all(bind=engine)

def generate_token():
    """生成随机token"""
    return secrets.token_urlsafe(32)

def create_user(username: str):
    """创建用户并生成token"""
    db = SessionLocal()
    try:
        # 检查用户名是否已存在
        existing_user = db.query(models.User).filter(
            models.User.username == username
        ).first()

        if existing_user:
            print(f"❌ 用户 '{username}' 已存在！")
            return

        # 生成token
        token = generate_token()

        # 创建用户
        new_user = models.User(
            username=username,
            token=token
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        print(f"✅ 用户创建成功！")
        print(f"用户名: {new_user.username}")
        print(f"Token: {new_user.token}")
        print(f"用户ID: {new_user.id}")
        print(f"\n请妥善保管此token，它将用于API认证。")

    except Exception as e:
        print(f"❌ 创建用户失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

def list_users():
    """列出所有用户"""
    db = SessionLocal()
    try:
        users = db.query(models.User).all()

        if not users:
            print("没有找到任何用户。")
            return

        print("\n用户列表:")
        print("-" * 80)
        print(f"{'ID':<5} {'用户名':<20} {'Token':<45} {'创建时间':<20}")
        print("-" * 80)

        for user in users:
            print(f"{user.id:<5} {user.username:<20} {user.token:<45} {user.created_at.strftime('%Y-%m-%d %H:%M:%S')}")

        print("-" * 80)
        print(f"共 {len(users)} 个用户")

    finally:
        db.close()

def delete_user(username: str):
    """删除用户"""
    db = SessionLocal()
    try:
        user = db.query(models.User).filter(
            models.User.username == username
        ).first()

        if not user:
            print(f"❌ 用户 '{username}' 不存在！")
            return

        # 确认删除
        confirm = input(f"确定要删除用户 '{username}' 吗？这将同时删除该用户的所有角色库和数据！(yes/no): ")

        if confirm.lower() != 'yes':
            print("取消删除。")
            return

        db.delete(user)
        db.commit()

        print(f"✅ 用户 '{username}' 已成功删除！")

    except Exception as e:
        print(f"❌ 删除用户失败: {str(e)}")
        db.rollback()
    finally:
        db.close()

def show_help():
    """显示帮助信息"""
    print("""
Story Creator - Token管理工具

用法:
    python manage_token.py <command> [arguments]

命令:
    create <username>   创建新用户并生成token
    list                列出所有用户
    delete <username>   删除指定用户
    help                显示此帮助信息

示例:
    python manage_token.py create alice
    python manage_token.py list
    python manage_token.py delete alice
    """)

def main():
    if len(sys.argv) < 2:
        show_help()
        return

    command = sys.argv[1].lower()

    if command == "create":
        if len(sys.argv) < 3:
            print("❌ 请提供用户名！")
            print("用法: python manage_token.py create <username>")
            return
        username = sys.argv[2]
        create_user(username)

    elif command == "list":
        list_users()

    elif command == "delete":
        if len(sys.argv) < 3:
            print("❌ 请提供用户名！")
            print("用法: python manage_token.py delete <username>")
            return
        username = sys.argv[2]
        delete_user(username)

    elif command == "help":
        show_help()

    else:
        print(f"❌ 未知命令: {command}")
        show_help()

if __name__ == "__main__":
    main()
