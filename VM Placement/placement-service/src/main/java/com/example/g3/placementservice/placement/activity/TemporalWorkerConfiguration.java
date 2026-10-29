package com.example.g3.placementservice.placement.activity;

import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowClientOptions;
import io.temporal.serviceclient.WorkflowServiceStubs;
import io.temporal.serviceclient.WorkflowServiceStubsOptions;
import io.temporal.worker.Worker;
import io.temporal.worker.WorkerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class TemporalWorkerConfiguration {

    @Bean(destroyMethod = "shutdown")
    WorkflowServiceStubs workflowServiceStubs(@Value("${temporal.address}") String address) {
        return WorkflowServiceStubs.newServiceStubs(
                WorkflowServiceStubsOptions.newBuilder().setTarget(address).build());
    }

    @Bean
    WorkflowClient workflowClient(
            WorkflowServiceStubs serviceStubs,
            @Value("${temporal.namespace:default}") String namespace) {
        return WorkflowClient.newInstance(
                serviceStubs,
                WorkflowClientOptions.newBuilder().setNamespace(namespace).build());
    }

    @Bean(destroyMethod = "shutdown")
    WorkerFactory placementWorkerFactory(
            WorkflowClient workflowClient,
            PlacementTemporalActivities activities,
            @Value("${temporal.task-queue}") String taskQueue) {
        WorkerFactory workerFactory = WorkerFactory.newInstance(workflowClient);
        Worker worker = workerFactory.newWorker(taskQueue);
        worker.registerActivitiesImplementations(activities);
        workerFactory.start();
        return workerFactory;
    }
}
