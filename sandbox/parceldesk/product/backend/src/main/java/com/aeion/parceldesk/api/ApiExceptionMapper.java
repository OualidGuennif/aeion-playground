package com.aeion.parceldesk.api;

import jakarta.ws.rs.WebApplicationException;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;
import jakarta.ws.rs.ext.ExceptionMapper;
import jakarta.ws.rs.ext.Provider;
import java.util.Map;

@Provider
public class ApiExceptionMapper implements ExceptionMapper<WebApplicationException> {

    @Override
    public Response toResponse(WebApplicationException error) {
        String message = error.getMessage() == null || error.getMessage().isBlank()
                ? "Request refused" : error.getMessage();
        return Response.fromResponse(error.getResponse())
                .type(MediaType.APPLICATION_JSON)
                .entity(Map.of("ok", false, "error", message))
                .build();
    }
}
