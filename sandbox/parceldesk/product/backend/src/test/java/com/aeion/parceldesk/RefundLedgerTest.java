package com.aeion.parceldesk;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.hasItem;
import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.notNullValue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.ws.rs.core.MediaType;
import org.junit.jupiter.api.Test;

@QuarkusTest
class RefundLedgerTest {

    @Test
    void theRefundLedgerServesRefundedOrdersNewestFirst() {
        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"reason\":\"Damaged in transit\"}")
                .when().post("/api/orders/PD-1049/refund")
                .then().statusCode(200);

        given()
                .when().get("/api/refunds")
                .then().statusCode(200)
                .body("id", hasItem("PD-1049"))
                .body("find { it.id == 'PD-1049' }.status", is("REFUNDED"))
                .body("find { it.id == 'PD-1049' }.refundAmountCents", is(11200))
                .body("find { it.id == 'PD-1049' }.refundReason", is("Damaged in transit"))
                .body("find { it.id == 'PD-1049' }.refundedAt", notNullValue());
    }
}
