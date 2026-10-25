package com.aeion.parceldesk.service;

import com.aeion.parceldesk.model.Order;
import com.aeion.parceldesk.model.OrderStatus;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.transaction.Transactional;
import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.NotFoundException;
import jakarta.ws.rs.WebApplicationException;
import jakarta.ws.rs.core.Response;
import java.time.Duration;
import java.time.Instant;

@ApplicationScoped
public class RefundService {

    public static final int WINDOW_DAYS = 30;
    public static final int HIGH_VALUE_CENTS = 20_000;

    private static WebApplicationException conflict(String message) {
        return new WebApplicationException(message, Response.Status.CONFLICT);
    }

    @Transactional
    public Order refund(String orderId, String reason, Instant now) {
        Order order = Order.findById(orderId);
        if (order == null) {
            throw new NotFoundException("Unknown order " + orderId);
        }
        if (order.status == OrderStatus.REFUNDED) {
            throw conflict("This order has already been refunded.");
        }
        if (order.status != OrderStatus.DELIVERED) {
            throw new BadRequestException("Only delivered orders can be refunded.");
        }
        if (order.deliveredAt == null) {
            throw new BadRequestException("This order has no delivery date.");
        }
        long age = Duration.between(order.deliveredAt, now).toDays();
        if (age < 0 || age > WINDOW_DAYS) {
            throw new BadRequestException("The refund window is " + WINDOW_DAYS + " days after delivery.");
        }
        int amount = order.totalCents - order.shippingCents;
        if (amount <= 0) {
            throw new BadRequestException("This order has nothing refundable.");
        }
        String note = reason == null ? "" : reason.trim();
        if (amount >= HIGH_VALUE_CENTS && note.isEmpty()) {
            throw new BadRequestException("A refund of 200.00 EUR or more requires a written reason.");
        }
        order.status = OrderStatus.REFUNDED;
        order.refundReason = note;
        order.refundAmountCents = amount;
        order.refundedAt = now;
        return order;
    }
}
