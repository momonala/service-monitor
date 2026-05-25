#!/bin/bash

# Define color codes
GREEN='\033[0;32m'
NO_COLOR='\033[0m'

set -e  # Exit immediately if a command exits with a non-zero status

echo -e "${GREEN}Updating package lists...${NO_COLOR}"
sudo apt-get update -y

echo -e "${GREEN}Upgrading installed packages...${NO_COLOR}"
sudo apt-get upgrade -y

echo -e "${GREEN}Performing full upgrade...${NO_COLOR}"
sudo apt-get dist-upgrade -y

echo -e "${GREEN}Installing git and bc...${NO_COLOR}"
sudo apt-get install bc git -y

echo -e "${GREEN}Removing unused packages...${NO_COLOR}"
sudo apt-get autoremove -y

echo -e "${GREEN}Cleaning up...${NO_COLOR}"
sudo apt-get clean

echo -e "${GREEN}Firmware update...${NO_COLOR}"
sudo apt full-upgrade

echo -e "${GREEN}Installing uv (Python package manager)...${NO_COLOR}"
if ! command -v uv &> /dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
else
    echo "✅ uv is already installed. Updating to latest version."
    uv self update
fi

echo -e "${GREEN}Installation process completed successfully!${NO_COLOR}"
