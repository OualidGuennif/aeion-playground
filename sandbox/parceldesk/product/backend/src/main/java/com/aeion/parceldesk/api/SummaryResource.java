package com.aeion.parceldesk.api;

import com.aeion.parceldesk.dto.SummaryView;
import com.aeion.parceldesk.model.Order;
import com.aeion.parceldesk.model.OrderStatus;
import com.aeion.parceldesk.model.PullRequest;
import com.aeion.parceldesk.model.PullRequestStatus;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Path("/api/summary")
public class SummaryResource {

    @GET
    public SummaryView summary() {
        List<Order> orders = Order.listAll();
        Map<String, Integer> counts = new LinkedHashMap<>();
        for (OrderStatus status : OrderStatus.values()) {
            counts.put(status.name().toLowerCase(), 0);
        }
        int revenue = 0;
        int refunded = 0;
        int refunds = 0;
        for (Order order : orders) {
            counts.merge(order.status.name().toLowerCase(), 1, Integer::sum);
            if (order.status != OrderStatus.REFUNDED) {
                revenue += order.totalCents;
            }
            if (order.refundAmountCents != null) {
                refunded += order.refundAmountCents;
                refunds += 1;
            }
        }
        long openPulls = PullRequest.count("status = ?1 or status = ?2",
                PullRequestStatus.OPEN, PullRequestStatus.APPROVED);
        long mergedPulls = PullRequest.count("status", PullRequestStatus.MERGED);
        return new SummaryView(orders.size(), counts, revenue, refunded, refunds, openPulls, mergedPulls);
    }
}
