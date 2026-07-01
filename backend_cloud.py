import os
import json
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from supabase import create_client, Client

# ===== КОНФИГУРАЦИЯ =====
# Эти данные нужно взять из панели Supabase (Settings → API)
SUPABASE_URL = "https://ВАШ_ПРОЕКТ.supabase.co"  # ЗАМЕНИТЕ!
SUPABASE_KEY = "ВАШ_ANON_KEY"  # ЗАМЕНИТЕ!

# ===== ПОДКЛЮЧЕНИЕ К БАЗЕ =====
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ===== МОДЕЛИ ДАННЫХ =====
class ProductBase(BaseModel):
    name: str
    protein: float = Field(..., ge=0)
    fat: float = Field(..., ge=0)
    carbs: float = Field(..., ge=0)
    kcal: float = Field(..., ge=0)

class Product(ProductBase):
    id: int

class IngredientItem(BaseModel):
    product_id: int
    weight_grams: float = Field(..., gt=0)

class RecipeBase(BaseModel):
    name: str
    ingredients: List[IngredientItem]

class Recipe(RecipeBase):
    id: int

class CalculateRequest(BaseModel):
    ingredients: List[IngredientItem]
    cooked_weight_grams: float = Field(..., gt=0)
    portion_weight_grams: float = Field(..., gt=0)

class CalculateResponse(BaseModel):
    per_100g: dict
    per_portion: dict
    total: dict

class HistoryEntry(BaseModel):
    recipe_name: str
    ingredients: List[dict]
    ingredients_count: int
    total_weight: float
    per100: dict
    total: dict
    cooking_method: str
    cooking_coefficient: float
    portion_calculated: bool = False
    portion_weight: Optional[float] = None
    portion: Optional[dict] = None

# ===== ПРИЛОЖЕНИЕ =====
app = FastAPI(title="КБЖУ Калькулятор Cloud", version="3.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== ЭНДПОИНТЫ ДЛЯ ПРОДУКТОВ =====
@app.get("/api/products", response_model=List[Product])
def list_products(search: Optional[str] = Query(None)):
    """Получить список продуктов"""
    query = supabase.table("products").select("*").order("name")
    if search:
        query = query.ilike("name", f"%{search}%")
    response = query.execute()
    return response.data

@app.post("/api/products", response_model=Product)
def create_product(product: ProductBase):
    """Добавить новый продукт"""
    response = supabase.table("products").insert(product.dict()).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Ошибка добавления продукта")
    return response.data[0]

@app.put("/api/products/{product_id}", response_model=Product)
def update_product(product_id: int, product: ProductBase):
    """Обновить продукт"""
    response = supabase.table("products").update(product.dict()).eq("id", product_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    return response.data[0]

@app.delete("/api/products/{product_id}")
def delete_product(product_id: int):
    """Удалить продукт"""
    response = supabase.table("products").delete().eq("id", product_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    return {"status": "ok"}

# ===== ЭНДПОИНТЫ ДЛЯ РЕЦЕПТОВ =====
@app.get("/api/recipes", response_model=List[Recipe])
def list_recipes():
    """Получить все рецепты"""
    response = supabase.table("recipes").select("*").order("name").execute()
    return response.data

@app.post("/api/recipes", response_model=Recipe)
def create_recipe(recipe: RecipeBase):
    """Сохранить рецепт"""
    data = {
        "name": recipe.name,
        "ingredients": [ing.dict() for ing in recipe.ingredients]
    }
    response = supabase.table("recipes").insert(data).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Ошибка сохранения рецепта")
    return response.data[0]

@app.put("/api/recipes/{recipe_id}", response_model=Recipe)
def update_recipe(recipe_id: int, recipe: RecipeBase):
    """Обновить рецепт"""
    data = {
        "name": recipe.name,
        "ingredients": [ing.dict() for ing in recipe.ingredients]
    }
    response = supabase.table("recipes").update(data).eq("id", recipe_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Рецепт не найден")
    return response.data[0]

@app.delete("/api/recipes/{recipe_id}")
def delete_recipe(recipe_id: int):
    """Удалить рецепт"""
    response = supabase.table("recipes").delete().eq("id", recipe_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Рецепт не найден")
    return {"status": "ok"}

# ===== ЭНДПОИНТЫ ДЛЯ ИСТОРИИ =====
@app.get("/api/history", response_model=List[dict])
def list_history():
    """Получить всю историю"""
    response = supabase.table("history").select("*").order("date", desc=True).execute()
    return response.data

@app.post("/api/history")
def add_history(entry: HistoryEntry):
    """Добавить запись в историю"""
    data = entry.dict()
    response = supabase.table("history").insert(data).execute()
    if not response.data:
        raise HTTPException(status_code=400, detail="Ошибка сохранения истории")
    return {"status": "ok"}

@app.delete("/api/history/{history_id}")
def delete_history(history_id: int):
    """Удалить запись из истории"""
    response = supabase.table("history").delete().eq("id", history_id).execute()
    if not response.data:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    return {"status": "ok"}

@app.delete("/api/history")
def clear_history():
    """Очистить всю историю"""
    supabase.table("history").delete().neq("id", 0).execute()
    return {"status": "ok"}

# ===== ЭНДПОИНТ ДЛЯ РАСЧЁТА =====
@app.post("/api/calculate", response_model=CalculateResponse)
def calculate(request: CalculateRequest):
    """Рассчитать КБЖУ блюда"""
    # Получаем все продукты из базы
    products_response = supabase.table("products").select("*").execute()
    all_products = {p["id"]: p for p in products_response.data}
    
    total_protein = total_fat = total_carbs = total_kcal = 0.0
    for item in request.ingredients:
        prod = all_products.get(item.product_id)
        if not prod:
            raise HTTPException(status_code=400, detail=f"Продукт с id {item.product_id} не найден")
        factor = item.weight_grams / 100.0
        total_protein += prod["protein"] * factor
        total_fat += prod["fat"] * factor
        total_carbs += prod["carbs"] * factor
        total_kcal += prod["kcal"] * factor

    cooked = request.cooked_weight_grams
    portion = request.portion_weight_grams

    per_100 = {
        "protein": (total_protein / cooked) * 100,
        "fat": (total_fat / cooked) * 100,
        "carbs": (total_carbs / cooked) * 100,
        "kcal": (total_kcal / cooked) * 100,
    }
    per_portion = {
        "protein": (total_protein / cooked) * portion,
        "fat": (total_fat / cooked) * portion,
        "carbs": (total_carbs / cooked) * portion,
        "kcal": (total_kcal / cooked) * portion,
    }
    total = {
        "protein": total_protein,
        "fat": total_fat,
        "carbs": total_carbs,
        "kcal": total_kcal,
    }
    return CalculateResponse(per_100g=per_100, per_portion=per_portion, total=total)

# ===== ЗАПУСК =====
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend_cloud:app", host="0.0.0.0", port=8000, reload=True)