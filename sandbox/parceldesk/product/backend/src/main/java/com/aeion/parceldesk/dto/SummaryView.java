package com.aeion.parceldesk.dto;

import java.util.Map;

public record SummaryView(
        int orders,
        Map<String, Integer> counts,
        int revenueCents,
        int refundedCents,
        int refunds,
        long openPulls,
        long mergedPulls) {
}
