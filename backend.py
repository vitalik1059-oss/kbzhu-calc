import csv
import os
import json
import io
import re
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, Body, UploadFile, File, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from filelock import FileLock

# ---------- Конфигурация ----------
CSV_FILE = Path("products_database.csv")
LOCK_FILE = CSV_FILE.with_suffix(".lock")
RECIPES_FILE = Path("recipes.json")
RECIPES_LOCK = RECIPES_FILE.with_suffix(".lock")
DELIMITER = ";"  # Точка с запятой для вашего CSV
DECIMAL_SEP = ","

# ---------- Модели данных (разрешаем нулевые значения) ----------
class ProductBase(BaseModel):
    name: str
    protein: float = Field(..., ge=0)
    fat: float = Field(..., ge=0)
    carbs: float = Field(..., ge=0)
    kcal: float = Field(..., ge=0)

class Product(ProductBase):
    id: int

class ProductUpdate(BaseModel):
    name: Optional[str] = None
    protein: Optional[float] = Field(None, ge=0)
    fat: Optional[float] = Field(None, ge=0)
    carbs: Optional[float] = Field(None, ge=0)
    kcal: Optional[float] = Field(None, ge=0)

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
    per_100g: ProductBase
    per_portion: ProductBase
    total: ProductBase

# ---------- Работа с CSV (продукты) ----------
def _parse_float(value: str) -> float:
    """Преобразует строку с запятой в float."""
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = str(value).strip().replace(',', '.')
    if not cleaned:
        return 0.0
    try:
        return float(cleaned)
    except ValueError:
        cleaned = re.sub(r'[^\d.]', '', cleaned)
        return float(cleaned) if cleaned else 0.0

def _format_float(value: float) -> str:
    return f"{value:.1f}".replace(".", DECIMAL_SEP)

def _read_csv() -> List[dict]:
    if not CSV_FILE.exists():
        with open(CSV_FILE, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, delimiter=DELIMITER)
            writer.writerow(["name", "protein", "fat", "carbs", "kcal"])
        return []
    rows = []
    with open(CSV_FILE, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=DELIMITER)
        for row in reader:
            rows.append({
                "name": row["name"].strip(),
                "protein": _parse_float(row["protein"]),
                "fat": _parse_float(row["fat"]),
                "carbs": _parse_float(row["carbs"]),
                "kcal": _parse_float(row["kcal"]),
            })
    return rows

def _write_csv(rows: List[dict]):
    with open(CSV_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "protein", "fat", "carbs", "kcal"], delimiter=DELIMITER)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "name": row["name"],
                "protein": _format_float(row["protein"]),
                "fat": _format_float(row["fat"]),
                "carbs": _format_float(row["carbs"]),
                "kcal": _format_float(row["kcal"]),
            })

def _get_all_products() -> List[dict]:
    with FileLock(LOCK_FILE):
        return _read_csv()

def _get_product_by_id(product_id: int) -> Optional[dict]:
    rows = _get_all_products()
    if 1 <= product_id <= len(rows):
        row = rows[product_id - 1].copy()
        row["id"] = product_id
        return row
    return None

def _add_product(product: ProductBase) -> int:
    with FileLock(LOCK_FILE):
        rows = _read_csv()
        rows.append(product.dict())
        _write_csv(rows)
        return len(rows)

def _update_product(product_id: int, update_data: ProductUpdate) -> bool:
    with FileLock(LOCK_FILE):
        rows = _read_csv()
        if product_id < 1 or product_id > len(rows):
            return False
        idx = product_id - 1
        current = rows[idx]
        if update_data.name is not None:
            current["name"] = update_data.name
        if update_data.protein is not None:
            current["protein"] = update_data.protein
        if update_data.fat is not None:
            current["fat"] = update_data.fat
        if update_data.carbs is not None:
            current["carbs"] = update_data.carbs
        if update_data.kcal is not None:
            current["kcal"] = update_data.kcal
        _write_csv(rows)
        return True

def _delete_product(product_id: int) -> bool:
    with FileLock(LOCK_FILE):
        rows = _read_csv()
        if product_id < 1 or product_id > len(rows):
            return False
        rows.pop(product_id - 1)
        _write_csv(rows)
        return True

# ---------- Работа с рецептами (JSON) ----------
def _read_recipes() -> List[dict]:
    if not RECIPES_FILE.exists():
        return []
    with FileLock(RECIPES_LOCK):
        with open(RECIPES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

def _write_recipes(recipes: List[dict]):
    with FileLock(RECIPES_LOCK):
        with open(RECIPES_FILE, "w", encoding="utf-8") as f:
            json.dump(recipes, f, ensure_ascii=False, indent=2)

def _get_recipe_by_id(recipe_id: int) -> Optional[dict]:
    recipes = _read_recipes()
    if 1 <= recipe_id <= len(recipes):
        recipe = recipes[recipe_id - 1].copy()
        recipe["id"] = recipe_id
        return recipe
    return None

def _add_recipe(recipe: RecipeBase) -> int:
    recipes = _read_recipes()
    recipes.append(recipe.dict())
    _write_recipes(recipes)
    return len(recipes)

def _update_recipe(recipe_id: int, recipe: RecipeBase) -> bool:
    recipes = _read_recipes()
    if recipe_id < 1 or recipe_id > len(recipes):
        return False
    recipes[recipe_id - 1] = recipe.dict()
    _write_recipes(recipes)
    return True

def _delete_recipe(recipe_id: int) -> bool:
    recipes = _read_recipes()
    if recipe_id < 1 or recipe_id > len(recipes):
        return False
    recipes.pop(recipe_id - 1)
    _write_recipes(recipes)
    return True

# ---------- FastAPI приложение ----------
app = FastAPI(title="КБЖУ Калькулятор", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

# ---------- Эндпоинты продуктов ----------
@app.get("/api/products", response_model=List[Product])
def list_products(search: Optional[str] = Query(None, min_length=1)):
    rows = _get_all_products()
    if search:
        search_lower = search.lower()
        rows = [r for r in rows if search_lower in r["name"].lower()]
    result = []
    for idx, row in enumerate(rows, start=1):
        result.append({**row, "id": idx})
    return result

@app.post("/api/products", response_model=Product, status_code=201)
def create_product(product: ProductBase):
    new_id = _add_product(product)
    return {**product.dict(), "id": new_id}

@app.get("/api/products/{product_id}", response_model=Product)
def get_product(product_id: int):
    product = _get_product_by_id(product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    return product

@app.put("/api/products/{product_id}", response_model=Product)
def update_product(product_id: int, update_data: ProductUpdate):
    success = _update_product(product_id, update_data)
    if not success:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    updated = _get_product_by_id(product_id)
    return updated

@app.delete("/api/products/{product_id}", status_code=204)
def delete_product(product_id: int):
    success = _delete_product(product_id)
    if not success:
        raise HTTPException(status_code=404, detail="Продукт не найден")
    return

# ---------- Эндпоинты рецептов ----------
@app.get("/api/recipes", response_model=List[Recipe])
def list_recipes():
    recipes = _read_recipes()
    result = []
    for idx, rec in enumerate(recipes, start=1):
        result.append({**rec, "id": idx})
    return result

@app.post("/api/recipes", response_model=Recipe, status_code=201)
def create_recipe(recipe: RecipeBase):
    new_id = _add_recipe(recipe)
    return {**recipe.dict(), "id": new_id}

@app.get("/api/recipes/{recipe_id}", response_model=Recipe)
def get_recipe(recipe_id: int):
    recipe = _get_recipe_by_id(recipe_id)
    if not recipe:
        raise HTTPException(status_code=404, detail="Рецепт не найден")
    return recipe

@app.put("/api/recipes/{recipe_id}", response_model=Recipe)
def update_recipe(recipe_id: int, recipe: RecipeBase):
    success = _update_recipe(recipe_id, recipe)
    if not success:
        raise HTTPException(status_code=404, detail="Рецепт не найден")
    return {**recipe.dict(), "id": recipe_id}

@app.delete("/api/recipes/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int):
    success = _delete_recipe(recipe_id)
    if not success:
        raise HTTPException(status_code=404, detail="Рецепт не найден")
    return

# ---------- Эндпоинт расчёта ----------
@app.post("/api/calculate", response_model=CalculateResponse)
def calculate(request: CalculateRequest):
    # Получаем все продукты и добавляем им ID
    all_products_list = _get_all_products()
    
    # Создаём словарь {id: продукт} с правильными ID (индекс + 1)
    all_products = {}
    for idx, prod in enumerate(all_products_list, start=1):
        all_products[idx] = prod
    
    total_protein = total_fat = total_carbs = total_kcal = 0.0
    
    for item in request.ingredients:
        prod = all_products.get(item.product_id)
        if not prod:
            raise HTTPException(
                status_code=400, 
                detail=f"Продукт с id {item.product_id} не найден"
            )
        factor = item.weight_grams / 100.0
        total_protein += prod["protein"] * factor
        total_fat += prod["fat"] * factor
        total_carbs += prod["carbs"] * factor
        total_kcal += prod["kcal"] * factor

    cooked = request.cooked_weight_grams
    portion = request.portion_weight_grams

    # На 100 г
    per_100 = {
        "protein": (total_protein / cooked) * 100,
        "fat": (total_fat / cooked) * 100,
        "carbs": (total_carbs / cooked) * 100,
        "kcal": (total_kcal / cooked) * 100,
    }
    
    # На порцию
    per_portion = {
        "protein": (total_protein / cooked) * portion,
        "fat": (total_fat / cooked) * portion,
        "carbs": (total_carbs / cooked) * portion,
        "kcal": (total_kcal / cooked) * portion,
    }
    
    # Всё блюдо
    total = {
        "protein": total_protein,
        "fat": total_fat,
        "carbs": total_carbs,
        "kcal": total_kcal,
    }
    
    return CalculateResponse(
        per_100g=ProductBase(name="На 100 г", **per_100),
        per_portion=ProductBase(name=f"На {portion} г", **per_portion),
        total=ProductBase(name="Всё блюдо", **total),
    )

# ---------- Экспорт / Импорт ----------
@app.get("/api/export/products")
def export_products():
    if not CSV_FILE.exists():
        raise HTTPException(status_code=404, detail="Файл базы продуктов не найден")
    with open(CSV_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    return Response(content=content, media_type="text/csv", headers={
        "Content-Disposition": f"attachment; filename={CSV_FILE.name}"
    })

@app.post("/api/import/products")
async def import_products(file: UploadFile = File(...)):
    if not file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="Требуется файл .csv")
    content = await file.read()
    try:
        decoded = content.decode("utf-8")
        reader = csv.DictReader(io.StringIO(decoded), delimiter=DELIMITER)
        rows = list(reader)
        if not rows:
            raise HTTPException(status_code=400, detail="Пустой файл")
        expected = {"name", "protein", "fat", "carbs", "kcal"}
        if not expected.issubset(set(reader.fieldnames or [])):
            raise HTTPException(status_code=400, detail="Неверные заголовки CSV. Ожидаются: name, protein, fat, carbs, kcal")
        new_rows = []
        for row in rows:
            new_rows.append({
                "name": row["name"].strip(),
                "protein": _parse_float(row["protein"]),
                "fat": _parse_float(row["fat"]),
                "carbs": _parse_float(row["carbs"]),
                "kcal": _parse_float(row["kcal"]),
            })
        with FileLock(LOCK_FILE):
            _write_csv(new_rows)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка импорта: {str(e)}")
    return {"status": "ok", "message": f"Импортировано {len(new_rows)} продуктов"}

@app.get("/api/export/recipes")
def export_recipes():
    recipes = _read_recipes()
    content = json.dumps(recipes, ensure_ascii=False, indent=2)
    return Response(content=content, media_type="application/json", headers={
        "Content-Disposition": "attachment; filename=recipes.json"
    })

@app.post("/api/import/recipes")
async def import_recipes(file: UploadFile = File(...)):
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Требуется файл .json")
    content = await file.read()
    try:
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, list):
            raise HTTPException(status_code=400, detail="Корневой элемент должен быть массивом")
        for item in data:
            if "name" not in item or "ingredients" not in item:
                raise HTTPException(status_code=400, detail="Каждый рецепт должен иметь поля 'name' и 'ingredients'")
            if not isinstance(item["ingredients"], list):
                raise HTTPException(status_code=400, detail="ingredients должен быть списком")
            for ing in item["ingredients"]:
                if "product_id" not in ing or "weight_grams" not in ing:
                    raise HTTPException(status_code=400, detail="Каждый ингредиент должен иметь product_id и weight_grams")
        with FileLock(RECIPES_LOCK):
            _write_recipes(data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Неверный формат JSON")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Ошибка импорта: {str(e)}")
    return {"status": "ok", "message": f"Импортировано {len(data)} рецептов"}

# ---------- Запуск ----------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend:app", host="127.0.0.1", port=8000, reload=True)