package com.aeion.parceldesk.service;

import com.aeion.parceldesk.model.Order;
import com.aeion.parceldesk.model.OrderStatus;
import io.quarkus.runtime.StartupEvent;
import jakarta.enterprise.context.ApplicationScoped;
import jakarta.enterprise.event.Observes;
import jakarta.transaction.Transactional;
import java.time.Instant;
import java.time.temporal.ChronoUnit;

@ApplicationScoped
public class SeedData {

    @Transactional
    void onStart(@Observes StartupEvent event) {
        if (Order.count() > 0) {
            return;
        }
        Instant now = Instant.now();
        add("PD-1042", "Camille Moreau", "Lyon", "Desk lamp", 2400, 6900, 500,
                OrderStatus.DELIVERED, now.minus(3, ChronoUnit.DAYS), null, null);
        add("PD-1043", "Hugo Bernard", "Nantes", "Ceramic vase", 1200, 4900, 500,
                OrderStatus.DELIVERED, now.minus(5, ChronoUnit.DAYS), null, null);
        add("PD-1044", "Lea Dubois", "Paris", "Oak stool", 4800, 13900, 900,
                OrderStatus.IN_TRANSIT, null, null, null);
        add("PD-1045", "Noah Petit", "Bordeaux", "Wool throw", 900, 3900, 500,
                OrderStatus.DELIVERED, now.minus(12, ChronoUnit.DAYS), null, null);
        add("PD-1046", "Ines Laurent", "Lille", "Book set", 3000, 8900, 700,
                OrderStatus.PACKING, null, null, null);
        add("PD-1047", "Gabriel Simon", "Strasbourg", "Floor lamp", 6200, 24900, 1200,
                OrderStatus.DELIVERED, now.minus(2, ChronoUnit.DAYS), null, null);
        add("PD-1048", "Jade Michel", "Rennes", "Glass carafe", 700, 2900, 500,
                OrderStatus.DELIVERED, now.minus(40, ChronoUnit.DAYS), null, null);
        add("PD-1049", "Louis Garcia", "Toulouse", "Linen sheets", 1800, 11900, 700,
                OrderStatus.DELIVERED, now.minus(20, ChronoUnit.DAYS), null, null);
        add("PD-1050", "Alice David", "Nice", "Desk organiser", 1100, 4500, 500,
                OrderStatus.REFUNDED, now.minus(8, ChronoUnit.DAYS), "Damaged in transit", 4000);
        add("PD-1051", "Raphael Roux", "Montpellier", "Wall clock", 1400, 5900, 500,
                OrderStatus.DELIVERED, now.minus(6, ChronoUnit.DAYS), null, null);
        add("PD-1052", "Emma Vincent", "Dijon", "Plant pot", 2600, 7400, 700,
                OrderStatus.IN_TRANSIT, null, null, null);
        add("PD-1053", "Arthur Lefevre", "Angers", "Table runner", 600, 3200, 500,
                OrderStatus.PACKING, null, null, null);
    }

    private void add(String id, String customer, String city, String item, int grams, int totalCents,
                     int shippingCents, OrderStatus status, Instant deliveredAt, String refundReason,
                     Integer refundAmountCents) {
        Order order = new Order();
        order.id = id;
        order.customer = customer;
        order.city = city;
        order.item = item;
        order.grams = grams;
        order.totalCents = totalCents;
        order.shippingCents = shippingCents;
        order.status = status;
        order.deliveredAt = deliveredAt;
        order.refundReason = refundReason;
        order.refundAmountCents = refundAmountCents;
        order.refundedAt = refundAmountCents == null || deliveredAt == null ? null : deliveredAt.plus(1, ChronoUnit.HOURS);
        order.persist();
    }
}
