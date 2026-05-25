#!/bin/bash
# Rename project files locally on Mac
# This updates service file names and references in code
# Usage: ./rename_project_local.sh <old_name> <new_name>
# Example: ./rename_project_local.sh servicemonitor service-monitor

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
old_service_file="install/projects_${old_name}.service"
new_service_file="install/projects_${new_name}.service"

echo -e "${GREEN}🔄 Renaming project files: ${old_name} → ${new_name}${NC}"
echo ""

# Check if running in git repo
if [ ! -d .git ]; then
    echo -e "${RED}❌ Error: Not in a git repository${NC}"
    exit 1
fi

# Check for uncommitted changes
if ! git diff-index --quiet HEAD -- 2>/dev/null; then
    echo -e "${YELLOW}⚠️  Warning: You have uncommitted changes${NC}"
    git status --short
    echo ""
    read -p "Continue anyway? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo -e "${RED}❌ Aborted${NC}"
        exit 1
    fi
fi

# Check if old service file exists
if [ ! -f "$old_service_file" ]; then
    echo -e "${RED}❌ Error: Service file not found: ${old_service_file}${NC}"
    exit 1
fi

echo -e "${GREEN}📋 Files that will be updated:${NC}"
echo "  - ${old_service_file} → ${new_service_file}"
echo "  - pyproject.toml (if name = \"${old_name}\")"
echo "  - install/install.sh (service_name variable)"
echo "  - Any README files with old name"
echo "  - src/canned_info.py (test fixtures)"
echo ""

read -p "Proceed with renaming? (y/N): " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo -e "${RED}❌ Aborted${NC}"
    exit 1
fi

# 1. Rename service file and update WorkingDirectory
echo -e "${GREEN}📝 Updating service file...${NC}"
if [ -f "$old_service_file" ]; then
    # Update WorkingDirectory path
    sed "s|WorkingDirectory=/home/mnalavadi/${old_name}|WorkingDirectory=/home/mnalavadi/${new_name}|g" \
        "$old_service_file" > "$new_service_file"
    
    # Also update any other references to old name in the service file
    sed -i.bak "s|/${old_name}/|/${new_name}/|g" "$new_service_file"
    rm -f "${new_service_file}.bak"
    
    echo -e "${GREEN}   Created: ${new_service_file}${NC}"
    
    # Git operations
    git rm "$old_service_file" 2>/dev/null || rm "$old_service_file"
    git add "$new_service_file"
fi

# 2. Update pyproject.toml
echo -e "${GREEN}📝 Updating pyproject.toml...${NC}"
if [ -f "pyproject.toml" ]; then
    if grep -q "name = \"${old_name}\"" pyproject.toml; then
        sed -i.bak "s|name = \"${old_name}\"|name = \"${new_name}\"|g" pyproject.toml
        rm -f pyproject.toml.bak
        echo -e "${GREEN}   Updated project name${NC}"
        git add pyproject.toml
    else
        echo -e "${YELLOW}   No changes needed${NC}"
    fi
fi

# 3. Update install.sh
echo -e "${GREEN}📝 Updating install/install.sh...${NC}"
if [ -f "install/install.sh" ]; then
    if grep -q "service_name=\"${old_name}\"" install/install.sh; then
        sed -i.bak "s|service_name=\"${old_name}\"|service_name=\"${new_name}\"|g" install/install.sh
        rm -f install/install.sh.bak
        echo -e "${GREEN}   Updated service_name variable${NC}"
        git add install/install.sh
    else
        echo -e "${YELLOW}   No changes needed${NC}"
    fi
fi

# 4. Update README files
echo -e "${GREEN}📝 Updating README files...${NC}"
readme_count=0
for readme in README*.md; do
    if [ -f "$readme" ]; then
        if grep -q "${old_name}" "$readme"; then
            # Update references but be careful with URLs
            sed -i.bak "s|projects_${old_name}\.service|projects_${new_name}.service|g" "$readme"
            sed -i.bak "s|/${old_name}/|/${new_name}/|g" "$readme"
            sed -i.bak "s|/${old_name}|/${new_name}|g" "$readme"
            rm -f "${readme}.bak"
            echo -e "${GREEN}   Updated: ${readme}${NC}"
            git add "$readme"
            readme_count=$((readme_count + 1))
        fi
    fi
done
if [ $readme_count -eq 0 ]; then
    echo -e "${YELLOW}   No README updates needed${NC}"
fi

# 5. Update test fixtures in src/canned_info.py
echo -e "${GREEN}📝 Updating test fixtures...${NC}"
if [ -f "src/canned_info.py" ]; then
    if grep -q "projects_${old_name}\.service" src/canned_info.py; then
        sed -i.bak "s|projects_${old_name}\.service|projects_${new_name}.service|g" src/canned_info.py
        rm -f src/canned_info.py.bak
        echo -e "${GREEN}   Updated: src/canned_info.py${NC}"
        git add src/canned_info.py
    else
        echo -e "${YELLOW}   No changes needed${NC}"
    fi
fi

# Show git status
echo ""
echo -e "${GREEN}📊 Git status:${NC}"
git status --short

echo ""
echo -e "${GREEN}✅ Local file updates completed! 🎉${NC}"
echo ""
echo -e "${GREEN}📝 Next steps:${NC}"
echo "   1. Review the changes: git diff --cached"
echo "   2. Test locally if possible: uv run src/app.py"
echo "   3. Commit: git commit -m \"Rename ${old_name} to ${new_name}\""
echo "   4. Push to remote: git push"
echo "   5. On Raspberry Pi:"
echo "      - cd /home/mnalavadi/${old_name}"
echo "      - mv /home/mnalavadi/${old_name} /home/mnalavadi/${new_name}"
echo "      - cd /home/mnalavadi/${new_name}"
echo "      - git pull"
echo "      - ./install/rename_service_pi.sh ${old_name} ${new_name}"
echo ""
