# ChessAnalyst

A web application that pulls your games from Lichess and Chess.com and runs personal analytics on them — opening repertoire, tilt patterns, time management, and opponent prep.

Built with Django as a portfolio / learning project.

---

## Prerequisites

[Python 3.12+](https://www.python.org/downloads/) installed.

[PostgreSQL](https://www.postgresql.org/download/) installed and running. After install, create a database named `chessanalyst`:

```sql
CREATE DATABASE chessanalyst;
```

During setup, `create_env.py` will ask for your PostgreSQL username, password, host, and port. Use values that can connect to this database.

---

## Setup

### Windows (PowerShell)

```powershell
# 1. Clone the repository from GitHub
git clone https://github.com/Ardakorkmaz0/ChessAnalyst.git

# 2. Navigate into the project directory
cd ChessAnalyst

# 3. Create a virtual environment named 'venv'
python -m venv venv

# 4. Activate the virtual environment
.\venv\Scripts\Activate.ps1

# 5. Install all required Python packages
pip install -r requirements.txt

# 6. Generate a secure .env file (SECRET_KEY etc.)
python create_env.py

# 7. Apply migrations to create database tables
python manage.py migrate

# 8. Create an administrative user
python manage.py createsuperuser

# 9. Start the Django development server
python manage.py runserver
```

### macOS / Linux (Bash)

```bash
# 1. Clone the repository from GitHub
git clone https://github.com/Ardakorkmaz0/ChessAnalyst.git

# 2. Navigate into the project directory
cd ChessAnalyst

# 3. Create a virtual environment named 'venv'
python3 -m venv venv

# 4. Activate the virtual environment
source venv/bin/activate

# 5. Install all required Python packages
pip install -r requirements.txt

# 6. Generate a secure .env file (SECRET_KEY etc.)
python create_env.py

# 7. Apply migrations to create database tables
python manage.py migrate

# 8. Create an administrative user
python manage.py createsuperuser

# 9. Start the Django development server
python manage.py runserver
```

Once started, visit `http://127.0.0.1:8000/` in your web browser.

---
