#!/bin/bash
# AlphaPulse — VPS Setup Script (Ubuntu 22.04)
# Run once on a fresh VPS to install all dependencies.

set -e

echo "===================================="
echo "  AlphaPulse VPS Setup"
echo "===================================="

# System packages
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3 python3-pip python3-venv \
    postgresql postgresql-contrib \
    git tmux curl wget

# Create project directory
mkdir -p ~/alphapulse
cd ~/alphapulse

# Python virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Python dependencies
pip install --upgrade pip
pip install -r requirements.txt

# PostgreSQL setup
sudo systemctl start postgresql
sudo systemctl enable postgresql

sudo -u postgres psql <<EOF
CREATE USER alphapulse WITH PASSWORD 'CHANGE_THIS_PASSWORD';
CREATE DATABASE alphapulse OWNER alphapulse;
GRANT ALL PRIVILEGES ON DATABASE alphapulse TO alphapulse;
EOF

echo ""
echo "PostgreSQL configured."
echo ""
echo "Next steps:"
echo "  1. Copy your project files to ~/alphapulse/"
echo "  2. Create .env from .env.example"
echo "  3. Run: python setup_db.py"
echo "  4. Start bot: tmux new -s alphapulse 'python main.py'"
echo "  5. Start dashboard: tmux new -s dashboard 'streamlit run dashboard/app.py'"
