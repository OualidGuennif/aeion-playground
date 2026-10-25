package com.aeion.parceldesk.dto;

import com.aeion.parceldesk.model.Order;

public record RefundView(OrderView order, int amountCents, String reason) {

    public static RefundView of(Order order) {
        return new RefundView(OrderView.of(order), order.refundAmountCents == null ? 0 : order.refundAmountCents,
                order.refundReason == null ? "" : order.refundReason);
    }
}
