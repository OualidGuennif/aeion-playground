package com.aeion.parceldesk.api;

import com.aeion.parceldesk.dto.OrderView;
import com.aeion.parceldesk.dto.RefundRequest;
import com.aeion.parceldesk.dto.RefundView;
import com.aeion.parceldesk.model.Order;
import com.aeion.parceldesk.model.OrderStatus;
import com.aeion.parceldesk.service.RefundService;
import jakarta.inject.Inject;
import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.NotFoundException;
import jakarta.ws.rs.POST;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.core.Response;
import io.quarkus.panache.common.Sort;
import java.time.Instant;
import java.util.List;

@Path("/api")
public class OrderResource {

    @Inject
    RefundService refunds;

    @GET
    @Path("/orders")
    public List<OrderView> orders() {
        return Order.<Order>list("order by id").stream().map(OrderView::of).toList();
    }

    @GET
    @Path("/orders/{id}")
    public OrderView order(@PathParam("id") String id) {
        Order order = Order.findById(id);
        if (order == null) {
            throw new NotFoundException("Unknown order " + id);
        }
        return OrderView.of(order);
    }

    @POST
    @Path("/orders/{id}/refund")
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

    @GET
    @Path("/refunds")
    public List<OrderView> refunds() {
        return Order.<Order>list("status", Sort.descending("refundedAt"), OrderStatus.REFUNDED)
                .stream()
                .map(OrderView::of)
                .toList();
    }

    @GET
    @Path("/refunds.csv")
    @Produces("text/csv")
    public Response refundsCsv() {
        StringBuilder csv = new StringBuilder("id,customer,city,refundAmountCents,refundReason,refundedAt\n");
        List<Order> refunded = Order.<Order>list("status", Sort.descending("refundedAt"), OrderStatus.REFUNDED);
        for (Order order : refunded) {
            OrderView view = OrderView.of(order);
            csv.append(csvLine(view)).append('\n');
        }
        return Response.ok(csv.toString())
                .header("Content-Disposition", "attachment; filename=\"refunds.csv\"")
                .build();
    }

    private static String csvLine(OrderView view) {
        return String.join(
                ",",
                csvField(view.id()),
                csvField(view.customer()),
                csvField(view.city()),
                csvField(view.refundAmountCents() == null ? null : String.valueOf(view.refundAmountCents())),
                csvField(view.refundReason()),
                csvField(view.refundedAt() == null ? null : view.refundedAt().toString()));
    }

    private static String csvField(String value) {
        if (value == null) {
            return "";
        }
        if (value.indexOf(',') >= 0
                || value.indexOf('"') >= 0
                || value.indexOf('\r') >= 0
                || value.indexOf('\n') >= 0) {
            return '"' + value.replace("\"", "\"\"") + '"';
        }
        return value;
    }
}
