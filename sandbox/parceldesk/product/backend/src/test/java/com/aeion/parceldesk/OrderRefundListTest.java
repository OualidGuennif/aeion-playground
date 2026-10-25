package com.aeion.parceldesk;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.is;
import static org.hamcrest.Matchers.notNullValue;
import static org.hamcrest.Matchers.nullValue;

import io.quarkus.test.junit.QuarkusTest;
import org.junit.jupiter.api.Test;

@QuarkusTest
class OrderRefundListTest {

    @Test
    void theOrderListExposesTheRefundDetailsOfTheRefundedOrder() {
        given()
                .when().get("/api/orders")
                .then().statusCode(200)
                .body("find { it.id == 'PD-1050' }.refundReason", is("Damaged in transit"))
                .body("find { it.id == 'PD-1050' }.refundAmountCents", is(4000))
                .body("find { it.id == 'PD-1050' }.refundedAt", notNullValue())
                .body("find { it.id == 'PD-1042' }.refundReason", nullValue())
                .body("find { it.id == 'PD-1042' }.refundAmountCents", nullValue())
                .body("find { it.id == 'PD-1042' }.refundedAt", nullValue());
    }
}
