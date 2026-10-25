package com.aeion.parceldesk.api;

import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import java.util.Map;

@Path("/api/health")
public class HealthResource {

    @GET
    public Map<String, Object> health() {
        return Map.of("status", "ok", "service", "parceldesk-api", "runtime", "quarkus");
    }
}
