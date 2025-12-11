#!/usr/bin/env python3
"""
Sync rating logic from rating_config.py to JavaScript frontend.
Run this script whenever you update rating thresholds.
"""
import os
from rating_config import RatingConfig

def sync_rating_logic():
    """Generate JavaScript rating function from Python config."""
    
    # Generate JavaScript code
    js_code = RatingConfig.export_for_javascript()
    
    # Write to frontend
    js_file = os.path.join(os.path.dirname(__file__), '../js/rating.js')
    with open(js_file, 'w') as f:
        f.write(js_code)
    
    print(f"✓ Generated {js_file}")
    print()
    print("Rating logic synchronized!")
    print("=" * 50)
    print("Updated files:")
    print("  - js/rating.js (generated)")
    print()
    print("Next steps:")
    print("  1. Update hourly.js to use rating.js")
    print("  2. Refresh browser to see changes")

if __name__ == '__main__':
    sync_rating_logic()
