@echo off
echo Створення структури проекту Irrigation_Expert_System...

:: Головна програма
mkdir main_app

:: Папка для автономних модулів
mkdir modules
mkdir modules\geo_module
mkdir modules\geo_module\data
mkdir modules\geo_module\cache
mkdir modules\hydraulic_module
mkdir modules\bom_module
mkdir modules\bom_module\library

:: Папка для проектів користувача
mkdir user_projects
mkdir user_projects\templates

:: Папка для спільних ресурсів та документації
mkdir docs
mkdir resources
mkdir resources\icons

echo. > architecture_plan.md
echo Структуру створено успішно!
pause