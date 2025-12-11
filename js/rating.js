// AUTO-GENERATED - DO NOT EDIT MANUALLY
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
