package com.aeion.parceldesk;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.is;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.ws.rs.core.MediaType;
import org.junit.jupiter.api.Test;

@QuarkusTest
class OrderApiTest {

    @Test
    void theOrderListAndSummaryAreServed() {
        given()
                .when().get("/api/orders")
                .then().statusCode(200)
                .body("size()", is(12))
                .body("[0].id", is("PD-1042"));

        given()
                .when().get("/api/summary")
                .then().statusCode(200)
                .body("orders", is(12))
                .body("counts.packing", is(2));
    }

    @Test
    void aRefundReturnsItsAmountAndReason() {
        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"reason\":\"Cracked on arrival\"}")
                .when().post("/api/orders/PD-1051/refund")
                .then().statusCode(200)
                .body("amountCents", is(5400))
                .body("reason", is("Cracked on arrival"));

        given()
                .when().get("/api/orders/PD-1051")
                .then().statusCode(200)
                .body("status", is("REFUNDED"));
    }

    @Test
    void aRefundWithAnUnknownOrderIsNotFound() {
        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"reason\":\"Unknown\"}")
                .when().post("/api/orders/PD-9999/refund")
                .then().statusCode(404)
                .body("ok", is(false));
    }
}
