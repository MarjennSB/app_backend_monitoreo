from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from jwt.exceptions import PyJWTError
from modules.auth.security import SECRET_KEY, ALGORITHM
from modules.auth.repository import UserRepository
from modules.auth.models import UserModel

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

async def get_current_user(token: str = Depends(oauth2_scheme)) -> UserModel:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No se pudo validar las credenciales",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            raise credentials_exception
        user_id: int = int(sub)
    except PyJWTError:
        raise credentials_exception
        
    user_repo = UserRepository()
    user = await user_repo.get_by_id(user_id)
    
    if user is None:
        raise credentials_exception
        
    if not user.status:
        raise HTTPException(status_code=400, detail="Usuario inactivo")
        
    return user
