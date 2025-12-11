"""
RATING CONFIGURATION - SINGLE SOURCE OF TRUTH
==============================================
This module defines the surf rating logic used across the entire application.
All rating calculations (backend and frontend) should derive from these rules.

To update rating logic, modify this file and run:
  python sync_rating_logic.py

This will automatically update:
  - rating_utils.py (Python backend)
  - js/rating.js (JavaScript frontend)
"""

class RatingConfig:
    """
    Centralized configuration for surf condition ratings.
    
    Rating is based on:
    - Swell height (meters)
    - Swell period (seconds)
    - Wind speed (knots)
    - Rating value = height × period
    
    Ratings are evaluated in order from top to bottom.
    Original logic from hourly.js includes wind conditions.
    """
    
    # Rating thresholds (evaluated in order)
    THRESHOLDS = [
        {
            'name': 'Epic',
            'color': '#a855f7',  # Purple
            'condition': lambda height, period, wind_speed: height > 2.5,
            'description': 'Large swell (>2.5m)'
        },
        {
            'name': 'Excellent',
            'color': '#15803d',  # Dark Green
            'condition': lambda height, period, wind_speed: (height * period) > 18 and wind_speed < 12,
            'description': 'High rating value with light wind'
        },
        {
            'name': 'Good',
            'color': '#22c55e',  # Green
            'condition': lambda height, period, wind_speed: (height * period) > 12 and wind_speed < 12,
            'description': 'Good conditions with light wind'
        },
        {
            'name': 'Fair',
            'color': '#eab308',  # Yellow
            'condition': lambda height, period, wind_speed: (height * period) > 6 and wind_speed < 10,
            'description': 'Marginal conditions'
        },
        {
            'name': 'Poor',
            'color': '#ef4444',  # Red
            'condition': lambda height, period, wind_speed: True,  # Default fallback
            'description': 'Not good surf conditions'
        }
    ]
    
    @classmethod
    def get_rating(cls, swell_height, swell_period, wind_speed=0):
        """
        Get rating and color for given swell conditions.
        
        Args:
            swell_height: Wave height in meters
            swell_period: Swell period in seconds
            wind_speed: Wind speed in knots (default: 0)
            
        Returns:
            tuple: (rating_name, color_hex)
        """
        for threshold in cls.THRESHOLDS:
            if threshold['condition'](swell_height, swell_period, wind_speed):
                return threshold['name'], threshold['color']
        
        # Should never reach here due to 'Poor' catchall, but just in case
        return 'Poor', '#ef4444'
    
    @classmethod
    def export_for_javascript(cls):
        """
        Export rating logic as JavaScript code.
        
        Returns:
            str: JavaScript function code
        """
        js_code = """// AUTO-GENERATED - DO NOT EDIT MANUALLY
// Generated from backend/rating_config.py
// To update, modify rating_config.py and run: python sync_rating_logic.py

function getRatingAndColor(swellHeight, swellPeriod, windSpeed = 0) {
    const height = swellHeight;
    const period = swellPeriod;
    const ratingVal = height * period;
    
    // Large swell (>2.5m)
    if (height > 2.5) {
        return { rating: 'Epic', color: '#a855f7' };
    }
    // High rating value with light wind
    else if (ratingVal > 18 && windSpeed < 12) {
        return { rating: 'Excellent', color: '#15803d' };
    }
    // Good conditions with light wind
    else if (ratingVal > 12 && windSpeed < 12) {
        return { rating: 'Good', color: '#22c55e' };
    }
    // Marginal conditions
    else if (ratingVal > 6 && windSpeed < 10) {
        return { rating: 'Fair', color: '#eab308' };
    }
    // Not good surf conditions (default)
    return { rating: 'Poor', color: '#ef4444' };
}

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = { getRatingAndColor };
}
"""
        
        return js_code


if __name__ == '__main__':
    # Test the configuration
    print("Rating Configuration Test")
    print("=" * 50)
    
    test_cases = [
        (0.5, 8.0, "Poor conditions"),
        (0.7, 7.0, "Fair conditions"),
        (1.2, 9.0, "Good conditions"),
        (1.5, 10.0, "Excellent conditions"),
        (2.8, 13.0, "Epic conditions"),
    ]
    
    for height, period, description in test_cases:
        rating, color = RatingConfig.get_rating(height, period)
        rating_val = height * period
        print(f"{description}:")
        print(f"  {height}m × {period}s = {rating_val:.1f}")
        print(f"  → {rating} ({color})")
        print()
