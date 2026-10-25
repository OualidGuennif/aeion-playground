package com.aeion.parceldesk;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;

import com.aeion.parceldesk.model.Order;
import com.aeion.parceldesk.model.OrderStatus;
import com.aeion.parceldesk.service.RefundService;
import io.quarkus.test.junit.QuarkusTest;
import jakarta.inject.Inject;
import jakarta.ws.rs.BadRequestException;
import jakarta.ws.rs.WebApplicationException;
import java.time.Instant;
import org.junit.jupiter.api.Test;

@QuarkusTest
class RefundRuleTest {

    @Inject
    RefundService refunds;

    @Test
    void aDeliveredOrderRefundsWithoutShipping() {
        Order order = refunds.refund("PD-1043", "Cracked on arrival", Instant.now());
        assertEquals(OrderStatus.REFUNDED, order.status);
        assertEquals(4400, order.refundAmountCents);
        assertEquals("Cracked on arrival", order.refundReason);
    }

    @Test
    void aHighValueRefundRequiresAWrittenReason() {
        assertThrows(BadRequestException.class, () -> refunds.refund("PD-1047", "  ", Instant.now()));
    }

    @Test
    void theWindowAndDuplicateRefundsAreRefused() {
        assertThrows(BadRequestException.class, () -> refunds.refund("PD-1048", "Too late", Instant.now()));
        assertThrows(WebApplicationException.class, () -> refunds.refund("PD-1050", "Again", Instant.now()));
    }
}
