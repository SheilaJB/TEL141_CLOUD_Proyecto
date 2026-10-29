package com.example.g3.placementservice.placement.domain;

public record ResourceVector(long vcpu, long ramMb, long diskMb) {

    public ResourceVector {
        if (vcpu < 0 || ramMb < 0 || diskMb < 0) {
            throw new IllegalArgumentException("Resource amounts cannot be negative");
        }
    }

    public static ResourceVector of(String resource, long amount) {
        return switch (resource) {
            case "vcpu" -> new ResourceVector(amount, 0, 0);
            case "vram_mb" -> new ResourceVector(0, amount, 0);
            case "vdisk_mb" -> new ResourceVector(0, 0, amount);
            default -> throw new IllegalStateException("Unsupported reserved resource: " + resource);
        };
    }

    public long get(String resource) {
        return switch (resource) {
            case "vcpu" -> vcpu;
            case "vram_mb" -> ramMb;
            case "vdisk_mb" -> diskMb;
            default -> throw new IllegalStateException("Unsupported reserved resource: " + resource);
        };
    }

    public boolean isZero() {
        return vcpu == 0 && ramMb == 0 && diskMb == 0;
    }

    public ResourceVector add(ResourceVector other) {
        return new ResourceVector(
                Math.addExact(vcpu, other.vcpu),
                Math.addExact(ramMb, other.ramMb),
                Math.addExact(diskMb, other.diskMb));
    }

    public ResourceVector subtract(ResourceVector other) {
        return new ResourceVector(
                Math.subtractExact(vcpu, other.vcpu),
                Math.subtractExact(ramMb, other.ramMb),
                Math.subtractExact(diskMb, other.diskMb));
    }

    public ResourceVector positiveDeltaTo(ResourceVector after) {
        return new ResourceVector(
                Math.max(0, after.vcpu - vcpu),
                Math.max(0, after.ramMb - ramMb),
                Math.max(0, after.diskMb - diskMb));
    }
}
