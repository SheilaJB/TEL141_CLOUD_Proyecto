FROM eclipse-temurin:24-jdk AS build

WORKDIR /app
COPY .mvn/ .mvn/
COPY mvnw pom.xml ./
COPY src/ src/
RUN chmod +x mvnw && ./mvnw -B -DskipTests package

FROM eclipse-temurin:24-jre

WORKDIR /app
COPY --from=build /app/target/placement-service-0.0.1-SNAPSHOT.jar app.jar
RUN useradd --system --uid 10001 appuser
USER appuser

ENTRYPOINT ["java", "-jar", "app.jar"]
