from typing import Optional
from passlib.hash import bcrypt
from modules.storage.database import db
from modules.auth.models import UserModel, RoleModel


class UserRepository:

    async def get_all(self):
        rows = await db.fetch_all(
            "SELECT id, username, first_name, last_name, email, role_id, status FROM users WHERE deleted_at IS NULL"
        )
        return [dict(r) for r in rows]

    async def get_by_email(self, email: str) -> Optional[UserModel]:
        row = await db.fetch_one(
            "SELECT * FROM users WHERE email = $1 AND deleted_at IS NULL", email
        )
        return UserModel.from_record(row) if row else None
        
    async def get_by_username(self, username: str) -> Optional[UserModel]:
        row = await db.fetch_one(
            "SELECT * FROM users WHERE username = $1 AND deleted_at IS NULL", username
        )
        return UserModel.from_record(row) if row else None

    async def get_by_id(self, user_id: int) -> Optional[UserModel]:
        row = await db.fetch_one(
            "SELECT * FROM users WHERE id = $1 AND deleted_at IS NULL", user_id
        )
        return UserModel.from_record(row) if row else None

    async def create_user(
        self, username: str, first_name: str, last_name: str, email: str, hashed_password: str, role_id: int = 2
    ) -> UserModel:
        row = await db.fetch_one(
            """
            INSERT INTO users (username, first_name, last_name, email, password, role_id, status)
            VALUES ($1, $2, $3, $4, $5, $6, true)
            RETURNING *
            """,
            username, first_name, last_name, email, hashed_password, role_id,
        )
        return UserModel.from_record(row)

    async def update_user(self, user_id: int, updates: dict) -> Optional[UserModel]:
        if not updates:
            return await self.get_by_id(user_id)
        
        set_clauses = []
        values = []
        for i, (key, value) in enumerate(updates.items(), start=1):
            set_clauses.append(f"{key} = ${i}")
            values.append(value)
            
        values.append(user_id)
        query = f"""
            UPDATE users 
            SET {", ".join(set_clauses)}, updated_at = NOW() 
            WHERE id = ${len(values)} AND deleted_at IS NULL 
            RETURNING *
        """
        row = await db.fetch_one(query, *values)
        return UserModel.from_record(row) if row else None

    async def toggle_status(self, user_id: int) -> Optional[UserModel]:
        row = await db.fetch_one(
            """
            UPDATE users 
            SET status = NOT status, updated_at = NOW() 
            WHERE id = $1 AND deleted_at IS NULL 
            RETURNING *
            """,
            user_id
        )
        return UserModel.from_record(row) if row else None


class RoleRepository:

    async def get_all(self):
        rows = await db.fetch_all(
            "SELECT * FROM roles WHERE deleted_at IS NULL AND status = true"
        )
        return [dict(r) for r in rows]

    async def get_by_id(self, role_id: int) -> Optional[RoleModel]:
        row = await db.fetch_one(
            "SELECT * FROM roles WHERE id = $1 AND deleted_at IS NULL", role_id
        )
        return RoleModel.from_record(row) if row else None
