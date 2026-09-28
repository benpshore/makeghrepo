fn greeting() -> String {
    String::from("Hello from quiet-otter!")
}

fn main() {
    println!("{}", greeting());
}

#[cfg(test)]
mod tests {
    use super::greeting;

    #[test]
    fn greets() {
        assert!(greeting().contains("quiet-otter"));
    }
}
