#!/bin/bash

# Name of your virtual environment folder
ENV_NAME="venv"

# Check if the virtual environment already exists
if [ -d "$ENV_NAME" ]; then
  echo "Virtual environment already exists. Deleting it..."
  rm -rf $ENV_NAME
fi

# Create a new virtual environment
echo "Creating a new virtual environment..."
python3 -m venv $ENV_NAME

# Activate the virtual environment
echo "Activating the virtual environment..."
source $ENV_NAME/bin/activate

# Upgrade pip
echo "Upgrading pip..."
pip install --upgrade pip

# Install dependencies from requirements.txt
if [ -f "requirements.txt" ]; then
  echo "Installing dependencies from requirements.txt..."
  pip install -r requirements.txt
else
  echo "requirements.txt not found!"
fi

# Done
echo "All set! Your virtual environment is ready to rock."
