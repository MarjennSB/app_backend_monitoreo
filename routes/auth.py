from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from datetime import timedelta
from modules.auth.repository import UserRepository, RoleRepository
from modules.auth.security import verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, get_password_hash
from modules.auth.dependencies import get_current_user
from modules.auth.models import UserModel, UserCreate, UserResponse

router = APIRouter()

# Map de role_id a nombre de rol
ROLE_NAMES = {
    1: "Administrador",
    2: "Usuario",
}


def _serialize_user(u: dict) -> dict:
    """
    Convierte un row de la BD al formato que espera el frontend Angular.
    """
    role_id = u.get("role_id", 2)
    return {
        "id": str(u["id"]),
        "username": u.get("username", ""),
        "email": u.get("email", ""),
        "first_name": u.get("first_name", ""),
        "last_name": u.get("last_name", ""),
        "is_active": bool(u.get("status", False)),
        "created_at": str(u.get("created_at", "")),
        "updated_at": str(u.get("updated_at", "")),
        "roles": [{"id": role_id, "name": ROLE_NAMES.get(role_id, "Desconocido")}],
    }


def _serialize_role(r: dict) -> dict:
    """Devuelve el rol en el formato que espera el frontend."""
    return {
        "id": r.get("id"),
        "name": r.get("name", ROLE_NAMES.get(r.get("id"), "Rol desconocido")),
    }


@router.post("/login", tags=["Auth"])
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user_repo = UserRepository()
    # Check if form_data.username is an email or a username
    user = await user_repo.get_by_email(form_data.username)
    if not user:
        user = await user_repo.get_by_username(form_data.username)

    if not user or not verify_password(form_data.password, user.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El usuario esta inactivo"
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)}, expires_delta=access_token_expires
    )

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "role_id": user.role_id
        }
    }


@router.get("/profile", tags=["Auth"])
async def read_users_me(current_user: UserModel = Depends(get_current_user)):
    role_name = ROLE_NAMES.get(current_user.role_id, "Desconocido")
    return {
        "id": str(current_user.id),
        "username": current_user.username,
        "email": current_user.email,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "is_active": current_user.status,
        "roles": [role_name],
        "created_at": str(current_user.created_at) if current_user.created_at else ""
    }


@router.post("/logout", tags=["Auth"])
async def logout(current_user: UserModel = Depends(get_current_user)):
    return {"message": "Logout exitoso. Por favor elimina el token en el cliente."}


@router.post("/register", response_model=UserResponse, tags=["Auth"])
async def register(user_data: UserCreate, current_user: UserModel = Depends(get_current_user)):
    if current_user.role_id != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos para registrar nuevos usuarios."
        )

    user_repo = UserRepository()
    existing_user = await user_repo.get_by_email(user_data.email)
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este correo ya se encuentra registrado."
        )
        
    existing_username = await user_repo.get_by_username(user_data.username)
    if existing_username:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Este nombre de usuario ya se encuentra registrado."
        )

    role_repo = RoleRepository()
    role_id = user_data.role_ids[0] if user_data.role_ids else 2
    role = await role_repo.get_by_id(role_id)
    if not role or not role.status:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El rol seleccionado es inválido o se encuentra inactivo."
        )

    hashed_password = get_password_hash(user_data.password)
    new_user = await user_repo.create_user(
        username=user_data.username,
        first_name=user_data.first_name,
        last_name=user_data.last_name,
        email=user_data.email,
        hashed_password=hashed_password,
        role_id=role_id
    )
    return new_user


from modules.auth.models import UserUpdate, ChangePasswordRequest

@router.post("/change-password", tags=["Auth"])
async def change_password(payload: ChangePasswordRequest, current_user: UserModel = Depends(get_current_user)):
    """Permite a un usuario autenticado cambiar su propia contraseña."""
    if not verify_password(payload.current_password, current_user.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual es incorrecta."
        )

    if payload.current_password == payload.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nueva contraseña no puede ser igual a la anterior."
        )

    user_repo = UserRepository()
    hashed_new_password = get_password_hash(payload.new_password)
    await user_repo.update_user(current_user.id, {"password": hashed_new_password})
    
    return {"message": "Contraseña actualizada con éxito."}

@router.post("/users/update/{user_id}", tags=["Usuarios"])
async def update_user(user_id: int, user_data: UserUpdate, current_user: UserModel = Depends(get_current_user)):
    if current_user.role_id != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos para modificar usuarios."
        )

    user_repo = UserRepository()
    user = await user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    updates = user_data.model_dump(exclude_unset=True)
    if "password" in updates and updates["password"]:
        updates["password"] = get_password_hash(updates["password"])
    elif "password" in updates:
        del updates["password"]

    if "role_ids" in updates:
        if updates["role_ids"]:
            updates["role_id"] = updates["role_ids"][0]
        del updates["role_ids"]

    updated_user = await user_repo.update_user(user_id, updates)
    return _serialize_user(updated_user.__dict__)


@router.get("/users/all", tags=["Usuarios"])
async def get_all_users():
    """Devuelve todos los usuarios formateados para el frontend Angular."""
    repo = UserRepository()
    users = await repo.get_all()
    return [_serialize_user(u) for u in users]


@router.post("/users/toggle/{user_id}", tags=["Usuarios"])
async def toggle_user_status(user_id: int, current_user: UserModel = Depends(get_current_user)):
    """Activa o desactiva a un usuario."""
    if current_user.role_id != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No tienes permisos para modificar usuarios."
        )
    if current_user.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No puedes desactivarte a ti mismo."
        )

    repo = UserRepository()
    updated_user = await repo.toggle_status(user_id)
    if not updated_user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
        
    return _serialize_user(updated_user.__dict__)


@router.get("/roles/all", tags=["Usuarios"])
async def get_all_roles():
    """Devuelve todos los roles disponibles desde la base de datos."""
    try:
        repo = RoleRepository()
        roles = await repo.get_all()
        return [_serialize_role(r) for r in roles] if roles else []
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error al cargar los roles de la base de datos"
        )
