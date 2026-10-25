FROM maven:3.9-eclipse-temurin-25 AS build
WORKDIR /build
COPY pom.xml .
RUN mvn -B -ntp -DskipTests dependency:go-offline
COPY src src
RUN mvn -B -ntp -DskipTests package

FROM eclipse-temurin:25-jre
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=build /build/target/quarkus-app/ /app/
RUN mkdir -p /data && chown 65532:65532 /data
USER 65532:65532
EXPOSE 8080
CMD ["sh", "-c", "java -XX:MaxRAMPercentage=70 -jar /app/quarkus-run.jar"]
