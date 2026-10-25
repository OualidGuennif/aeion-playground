package com.aeion.parceldesk.model;

import io.quarkus.hibernate.orm.panache.PanacheEntityBase;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.Id;
import jakarta.persistence.Table;
import java.time.Instant;

@Entity
@Table(name = "orders")
public class Order extends PanacheEntityBase {

    @Id
    public String id;

    public String customer;
    public String city;
    public String item;
    public int grams;
    public int totalCents;
    public int shippingCents;

    @Enumerated(EnumType.STRING)
    public OrderStatus status;

    public Instant deliveredAt;
    public String refundReason;
    public Integer refundAmountCents;
    public Instant refundedAt;
}
