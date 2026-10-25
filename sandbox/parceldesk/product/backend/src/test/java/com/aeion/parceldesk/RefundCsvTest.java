package com.aeion.parceldesk;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.containsString;
import static org.hamcrest.Matchers.is;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.ws.rs.core.MediaType;
import org.junit.jupiter.api.Test;

@QuarkusTest
class RefundCsvTest {

    private static final String HEADER = "id,customer,city,refundAmountCents,refundReason,refundedAt";

    @Test
    void theCsvExportServesTheHeaderAndOnlyRefundedOrders() {
        String csv = given()
                .when().get("/api/refunds.csv")
                .then().statusCode(200)
                .contentType(containsString("text/csv"))
                .header("Content-Disposition", is("attachment; filename=\"refunds.csv\""))
                .extract().asString();

        assertEquals(HEADER, csv.lines().findFirst().orElse(""), csv);
        assertTrue(csv.contains("PD-1050,Alice David,Nice,4000,Damaged in transit,"), csv);
        assertFalse(csv.contains("PD-1042,"), csv);
    }

    @Test
    void theCsvExportQuotesCommasAndQuotesInRefundReasons() {
        String reason = "Cracked, \"fragile\" item";
        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"reason\":\"Cracked, \\\"fragile\\\" item\"}")
                .when().post("/api/orders/PD-1045/refund")
                .then().statusCode(200)
                .body("reason", is(reason));

        String csv = given()
                .when().get("/api/refunds.csv")
                .then().statusCode(200)
                .extract().asString();

        assertTrue(csv.contains("PD-1045,Noah Petit,Bordeaux,3400,\"Cracked, \"\"fragile\"\" item\","), csv);
    }
}
