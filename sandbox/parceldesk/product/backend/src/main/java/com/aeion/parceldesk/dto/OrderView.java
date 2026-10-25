package com.aeion.parceldesk.dto;

import com.aeion.parceldesk.model.Order;
import java.time.Instant;

public record OrderView(
        String id,
        String customer,
        String city,
        String item,
        int grams,
        int totalCents,
        int shippingCents,
        String status,
        Instant deliveredAt,
        String refundReason,
        Integer refundAmountCents,
        Instant refundedAt) {

    public static OrderView of(Order order) {
        return new OrderView(
                order.id,
                order.customer,
                order.city,
                order.item,
                order.grams,
                order.totalCents,
                order.shippingCents,
                order.status.name(),
                order.deliveredAt,
                order.refundReason,
                order.refundAmountCents,
                order.refundedAt);
    }
}
