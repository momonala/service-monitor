#!/bin/bash
# Rename systemd service on Raspberry Pi
# Run this AFTER pulling updated code with new service file
# Usage: ./rename_service_pi.sh <old_name> <new_name>
# Example: ./rename_service_pi.sh servicemonitor service-monitor

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check arguments
if [ $# -ne 2 ]; then
    echo -e "${RED}❌ Usage: $0 <old_name> <new_name>${NC}"
    echo "Example: $0 servicemonitor service-monitor"
    exit 1
fi

old_name="$1"
new_name="$2"
old_service="projects_${old_name}.service"
new_service="projects_${new_name}.service"
systemd_dir="/lib/systemd/system"
old_service_path="${systemd_dir}/${old_service}"
new_service_path="${systemd_dir}/${new_service}"
new_service_file="install/${new_service}"

echo -e "${GREEN}🔄 Migrating systemd service: ${old_service} → ${new_service}${NC}"
echo ""

# Check if running on Linux with systemd
if [ ! -d "$systemd_dir" ]; then
    echo -e "${RED}❌ Error: systemd directory not found. Are you on Linux?${NC}"
    exit 1
fi

# Check if new service file exists in install/
if [ ! -f "$new_service_file" ]; then
    echo -e "${RED}❌ Error: New service file not found: ${new_service_file}${NC}"
    echo -e "${RED}   Did you git pull the latest changes?${NC}"
    exit 1
fi

# Check if old service exists in systemd
if [ ! -f "$old_service_path" ]; then
    echo -e "${YELLOW}⚠️  Warning: Old service not found in systemd: ${old_service_path}${NC}"
    echo -e "${YELLOW}   Will proceed with installing new service only${NC}"
    old_exists=false
else
    old_exists=true
fi

# Show what will happen
echo -e "${GREEN}📋 Migration plan:${NC}"
if [ "$old_exists" = true ]; then
    echo "  1. Stop ${old_service}"
    echo "  2. Disable ${old_service}"
fi
echo "  3. Install ${new_service} to ${systemd_dir}"
echo "  4. Reload systemd daemon"
echo "  5. Enable ${new_service}"
echo "  6. Start ${new_service}"
if [ "$old_exists" = true ]; then
    echo "  7. Optionally remove ${old_service}"
fi
echo ""

# Show new service file content
echo -e "${GREEN}📄 New service file content:${NC}"
cat "$new_service_file"
echo ""

# Confirm before proceeding
read -p "Proceed with migration? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}❌ Aborted${NC}"
    exit 1
fi

# Stop and disable old service (if it exists)
if [ "$old_exists" = true ]; then
    echo -e "${GREEN}⏹️  Stopping old service: ${old_service}${NC}"
    if systemctl is-active --quiet "$old_service"; then
        sudo systemctl stop "$old_service"
        echo -e "${GREEN}   Service stopped${NC}"
    else
        echo -e "${YELLOW}   Service was not running${NC}"
    fi

    echo -e "${GREEN}🚫 Disabling old service: ${old_service}${NC}"
    if systemctl is-enabled --quiet "$old_service" 2>/dev/null; then
        sudo systemctl disable "$old_service"
        echo -e "${GREEN}   Service disabled${NC}"
    else
        echo -e "${YELLOW}   Service was not enabled${NC}"
    fi
fi

# Install new service file
echo -e "${GREEN}📝 Installing new service file: ${new_service_path}${NC}"
sudo cp "$new_service_file" "$new_service_path"

# Set permissions
echo -e "${GREEN}🔒 Setting permissions...${NC}"
sudo chmod 644 "$new_service_path"

# Reload systemd
echo -e "${GREEN}🔄 Reloading systemd daemon...${NC}"
sudo systemctl daemon-reload

# Enable new service
echo -e "${GREEN}✅ Enabling new service: ${new_service}${NC}"
sudo systemctl enable "$new_service"

# Start new service
echo -e "${GREEN}▶️  Starting new service: ${new_service}${NC}"
sudo systemctl start "$new_service"

# Wait a moment for service to start
sleep 2

# Show status
echo ""
echo -e "${GREEN}📊 New service status:${NC}"
sudo systemctl status "$new_service" --no-pager || true

# Check if service is running
echo ""
if systemctl is-active --quiet "$new_service"; then
    echo -e "${GREEN}✅ Service is running successfully!${NC}"
else
    echo -e "${RED}❌ Warning: Service may not have started properly${NC}"
    echo -e "${YELLOW}   Check logs with: journalctl -u ${new_service} -n 50${NC}"
fi

# Cleanup old service file
if [ "$old_exists" = true ]; then
    echo ""
    echo -e "${YELLOW}🗑️  Old service file still exists: ${old_service_path}${NC}"
    read -p "Remove old service file? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        sudo rm "$old_service_path"
        sudo systemctl daemon-reload
        echo -e "${GREEN}✅ Old service file removed${NC}"
        
        # Clear systemd's memory of the old service
        echo -e "${GREEN}🧹 Clearing systemd memory of old service...${NC}"
        sudo systemctl reset-failed "$old_service" 2>/dev/null || true
        echo -e "${GREEN}✅ Old service cleared from systemd${NC}"
    else
        echo -e "${YELLOW}⚠️  Old service file kept. Remove manually with:${NC}"
        echo -e "${YELLOW}   sudo rm ${old_service_path}${NC}"
        echo -e "${YELLOW}   sudo systemctl daemon-reload${NC}"
        echo -e "${YELLOW}   sudo systemctl reset-failed ${old_service}${NC}"
    fi
fi

# Always try to clean up any ghost entries (in case old service was already removed)
echo ""
echo -e "${GREEN}🧹 Cleaning up any systemd ghost entries...${NC}"
sudo systemctl reset-failed "$old_service" 2>/dev/null && echo -e "${GREEN}✅ Cleared ghost entry for ${old_service}${NC}" || echo -e "${YELLOW}   No ghost entries found${NC}"

echo ""
echo -e "${GREEN}✅ Service migration completed successfully! 🎉${NC}"
echo ""
echo -e "${GREEN}📝 Useful commands:${NC}"
echo "   Check status:     sudo systemctl status ${new_service}"
echo "   View logs:        journalctl -u ${new_service} -f"
echo "   Restart service:  sudo systemctl restart ${new_service}"
if [ "$old_exists" = true ]; then
    echo ""
    echo -e "${YELLOW}📌 Old service logs still available with:${NC}"
    echo "   journalctl -u ${old_service}"
fi
echo ""
