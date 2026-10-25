package com.aeion.parceldesk.api;

import com.aeion.parceldesk.dto.OrderView;
import com.aeion.parceldesk.dto.RefundRequest;
import com.aeion.parceldesk.dto.RefundView;
import com.aeion.parceldesk.model.Order;
import com.aeion.parceldesk.service.RefundService;
import jakarta.inject.Inject;
import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.NotFoundException;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import java.time.Instant;
import java.util.List;

@Path("/api/orders")
public class OrderResource {

    @Inject
    RefundService refunds;

    @GET
    public List<OrderView> orders() {
        return Order.<Order>list("order by id").stream().map(OrderView::of).toList();
    }

    @GET
    @Path("/{id}")
    public OrderView order(@PathParam("id") String id) {
        Order order = Order.findById(id);
        if (order == null) {
            throw new NotFoundException("Unknown order " + id);
        }
        return OrderView.of(order);
    }

    @POST
    @Path("/{id}/refund")
    public RefundView refund(@PathParam("id") String id, RefundRequest request) {
        if (request == null) {
            throw new BadRequestException("A refund request body is required.");
        }
        if (request.reason() != null && request.reason().length() > 500) {
            throw new BadRequestException("A refund reason must be at most 500 characters.");
        }
        Order order = refunds.refund(id, request.reason(), Instant.now());
        return RefundView.of(order);
    }
}
