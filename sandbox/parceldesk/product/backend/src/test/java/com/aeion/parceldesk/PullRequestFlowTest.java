package com.aeion.parceldesk;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.is;

import io.quarkus.test.junit.QuarkusTest;
import jakarta.ws.rs.core.MediaType;
import java.util.UUID;
import org.junit.jupiter.api.Test;

@QuarkusTest
class PullRequestFlowTest {

    @Test
    void aGreenPullRequestCanBeReviewedAndMergedOnce() {
        String mission = "PD-T" + UUID.randomUUID().toString().substring(0, 8);
        String id = given()
                .contentType(MediaType.APPLICATION_JSON)
                .body(body(mission, 3, 3))
                .when().post("/api/pulls")
                .then().statusCode(200)
                .body("status", is("OPEN"))
                .extract().path("id");

        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"verdict\":\"OK\",\"comment\":\"Checks and diff agree.\",\"reviewer\":\"operator\"}")
                .when().post("/api/pulls/" + id + "/review")
                .then().statusCode(200)
                .body("status", is("APPROVED"));

        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"mergedBy\":\"operator\"}")
                .when().post("/api/pulls/" + id + "/merge")
                .then().statusCode(200)
                .body("status", is("MERGED"))
                .body("mergedBy", is("operator"));

        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"mergedBy\":\"operator\"}")
                .when().post("/api/pulls/" + id + "/merge")
                .then().statusCode(409);
    }

    @Test
    void redChecksCannotBeMerged() {
        String mission = "PD-T" + UUID.randomUUID().toString().substring(0, 8);
        String id = given()
                .contentType(MediaType.APPLICATION_JSON)
                .body(body(mission, 2, 3))
                .when().post("/api/pulls")
                .then().statusCode(200)
                .body("status", is("BLOCKED"))
                .extract().path("id");

        given()
                .contentType(MediaType.APPLICATION_JSON)
                .body("{\"mergedBy\":\"operator\"}")
                .when().post("/api/pulls/" + id + "/merge")
                .then().statusCode(409);
    }

    private String body(String mission, int passed, int total) {
        return """
                {
                  "missionId": "%s",
                  "ticketId": "TCK-TEST",
                  "title": "Expose refund metadata in the order list",
                  "risk": "low",
                  "diff": "diff --git a/OrderView.java b/OrderView.java",
                  "commitHash": "%s",
                  "packetDigest": "sha256:%s",
                  "branch": "factory/%s",
                  "base": "%s",
                  "checks": {
                    "tests": {"passed": %d, "total": %d},
                    "gateApproved": true,
                    "gateDecision": "approved"
                  },
                  "review": {"verdict": "OK", "comment": "Reviewed", "model": "glm-5.3-flash"}
                }
                """.formatted(mission, "a".repeat(40), "b".repeat(64), mission, "c".repeat(40), passed, total);
    }
}
